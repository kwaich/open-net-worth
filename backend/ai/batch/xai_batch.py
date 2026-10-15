"""xAI (Grok) Batch API — batch submit + collect hooks.

xAI's ``/v1/batches`` is NOT OpenAI-shape (docs.x.ai/developers/advanced-api-usage/
batch-api, verified 2026-07-15 against the incident that motivated this module):
it REQUIRES a ``name`` field, does NOT accept ``endpoint``/``completion_window``,
and serves results via a paginated ``GET /v1/batches/{id}/results`` instead of an
``output_file_id`` file download. There is no OpenAI-SDK-compatible batch client for
it, so this adapter speaks raw HTTP (httpx) directly.

The per-line JSONL request body IS the same ``/v1/chat/completions`` shape OpenAI
uses, so body-building/result-extraction is shared with ``openai_batch`` via
``_openai_shape`` — only the batch-job lifecycle differs.

Credential/base_url resolution mirrors ``openai_batch``: the active
``ModelConfigStore.resolved()`` instance (this adapter is only ever invoked once
``ai/batch/routing.py`` has already matched the active config to xAI's host).
"""

from __future__ import annotations

import json
import logging

import httpx

from backend.ai.batch._openai_shape import build_chat_body, extract_chat_result
from backend.core.llm_seam import BatchPending, BatchRequest, BatchResult

logger = logging.getLogger(__name__)

_DEFAULT_BASE_URL = "https://api.x.ai/v1"
_TIMEOUT = httpx.Timeout(30.0)
_RESULTS_PAGE_SIZE = 100

# A non-advancing/repeating pagination_token (buggy or hostile endpoint) must never
# hang the reap sweep's shared event loop forever — cap total pages fetched per
# collect() call. On breach, log a warning and return whatever was accumulated so
# far (a partial result set is still useful; the caller marks the row failed/retries
# rather than losing everything silently).
_MAX_RESULT_PAGES = 100


def _resolved_creds() -> tuple[str, str]:
    from backend.db.model_config_store import ModelConfigStore

    inst = ModelConfigStore().resolved()
    api_key = inst.get("api_key") or ""
    base_url = (inst.get("base_url") or _DEFAULT_BASE_URL).rstrip("/")
    return api_key, base_url


def _client(api_key: str, base_url: str) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=base_url,
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=_TIMEOUT,
    )


async def submit(task: str, requests: list[BatchRequest]) -> str:
    """Upload a JSONL file then create a named xAI batch job. Returns the batch id."""
    from backend.db.model_config_store import ModelConfigStore

    default_model = ModelConfigStore().resolved()["model"]
    api_key, base_url = _resolved_creds()

    lines = [
        json.dumps(
            {
                "custom_id": req.custom_id,
                "method": "POST",
                "url": "/v1/chat/completions",
                "body": build_chat_body(req, default_model),
            }
        )
        for req in requests
    ]
    jsonl_bytes = ("\n".join(lines)).encode("utf-8")

    async with _client(api_key, base_url) as client:
        upload = await client.post(
            "/files", files={"file": ("batch.jsonl", jsonl_bytes, "application/jsonl")}
        )
        upload.raise_for_status()
        file_id = upload.json()["id"]

        created = await client.post(
            "/batches",
            json={"name": f"onw-{task}-{requests[0].custom_id}", "input_file_id": file_id},
        )
        created.raise_for_status()
        batch_id = created.json()["batch_id"]

    logger.info("xai_batch: submitted %d requests — batch_id=%s", len(requests), batch_id)
    return batch_id


async def collect(handle: str) -> list[BatchResult] | BatchPending:
    """Poll an xAI batch by id. Returns results (paginated fetch) or BatchPending."""
    api_key, base_url = _resolved_creds()

    async with _client(api_key, base_url) as client:
        status = await client.get(f"/batches/{handle}")
        status.raise_for_status()
        state = status.json().get("state") or {}
        if int(state.get("num_pending") or 0) > 0:
            return BatchPending()

        results = await _fetch_all_results(client, handle)

    logger.info("xai_batch: collected %d results from batch %s", len(results), handle)
    return results


async def _fetch_all_results(client: httpx.AsyncClient, handle: str) -> list[BatchResult]:
    """Fetch every page of ``/batches/{handle}/results``.

    Bounded at ``_MAX_RESULT_PAGES`` — this runs on the scheduler's shared event
    loop with no outer timeout (``scheduler._run_reap_batches`` awaits
    ``collect_batch`` directly), so a non-advancing/repeating token from a buggy or
    hostile endpoint must never turn into an infinite loop. On breach (cap hit, or
    the token repeats verbatim — a stronger non-advancing signal than the cap
    alone) this logs a WARNING and returns whatever was accumulated so far rather
    than hanging or growing memory without bound.
    """
    results: list[BatchResult] = []
    pagination_token: str | None = None
    for _ in range(_MAX_RESULT_PAGES):
        params: dict = {"limit": _RESULTS_PAGE_SIZE}
        if pagination_token:
            params["pagination_token"] = pagination_token
        page = await client.get(f"/batches/{handle}/results", params=params)
        page.raise_for_status()
        payload = page.json()
        results.extend(_parse_result_item(item) for item in payload.get("results") or [])
        next_token = payload.get("pagination_token")
        if not next_token:
            return results
        if next_token == pagination_token:
            logger.warning(
                "xai_batch: batch %s pagination_token did not advance — stopping "
                "early with %d result(s)",
                handle,
                len(results),
            )
            return results
        pagination_token = next_token

    logger.warning(
        "xai_batch: batch %s hit the %d-page result cap — returning %d result(s), "
        "possible non-terminating pagination",
        handle,
        _MAX_RESULT_PAGES,
        len(results),
    )
    return results


def _parse_result_item(item: dict) -> BatchResult:
    """Parse one ``results[]`` entry. Docs are inconsistent about the id field name
    across examples (``custom_id`` vs ``batch_request_id``) — accept either."""
    from backend.ai.cost_tracker import record as _record
    from backend.db.model_config_store import provider_label_for

    custom_id = item.get("custom_id") or item.get("batch_request_id") or ""
    error = item.get("error_message")
    if error:
        logger.warning("xai_batch: item %s failed: %s", custom_id, error)
        return BatchResult(custom_id=custom_id, text=None, error=str(error))

    response = (item.get("batch_result") or {}).get("response") or {}
    # Chat-completions results are wrapped under "chat_get_completion"; fall back to
    # treating `response` itself as the body if it already looks like one (choices
    # present) in case the wrapper key changes in a future API revision.
    body = response.get("chat_get_completion") or response
    text, stop_reason, usage = extract_chat_result(body)
    if usage:
        _record(
            "batch",
            body.get("model", ""),
            usage.get("prompt_tokens", 0),
            usage.get("completion_tokens", 0),
            batch=True,
            provider=provider_label_for("openai_compatible", _DEFAULT_BASE_URL),
        )
    return BatchResult(custom_id=custom_id, text=text, stop_reason=stop_reason)
