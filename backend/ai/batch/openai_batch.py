"""OpenAI native Batch API — batch submit + collect hooks.

Routed here (see ``ai/batch/routing.py`` + ``ai/batch/dispatch.py``) only when the
active provider resolves to the real OpenAI endpoint (``api.openai.com``) — every
other ``openai_compatible`` endpoint (xAI, LiteLLM, Ollama, a custom gateway, ...)
is a DIFFERENT batch API shape (or has none) and is routed elsewhere / to the
seam's sync fallback instead of assuming this module's OpenAI-native shape applies.
"""

from __future__ import annotations

import json
import logging
import tempfile

from backend.ai.batch._openai_shape import build_chat_body, extract_chat_result
from backend.core.llm_seam import BatchPending, BatchRequest, BatchResult

logger = logging.getLogger(__name__)

# OpenAI batch states that indicate terminal failure.
_FAILED_STATES = {"failed", "expired", "cancelled", "cancelling"}


async def submit(task: str, requests: list[BatchRequest]) -> str:
    """Submit a list of BatchRequest items to the OpenAI Batch API.

    Uploads a JSONL file then creates a batch job.  Returns the batch id.
    """
    import openai

    from backend.db.model_config_store import ModelConfigStore

    cfg = ModelConfigStore()
    inst = cfg.resolved()
    api_key = inst.get("api_key") or ""
    base_url = inst.get("base_url") or ""

    client = openai.AsyncOpenAI(api_key=api_key or None, base_url=base_url or None)

    lines = []
    for req in requests:
        body = build_chat_body(req, inst["model"])
        lines.append(
            json.dumps(
                {
                    "custom_id": req.custom_id,
                    "method": "POST",
                    "url": "/v1/chat/completions",
                    "body": body,
                }
            )
        )

    jsonl_bytes = "\n".join(lines).encode("utf-8")
    with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as tmp:
        tmp.write(jsonl_bytes)
        tmp_path = tmp.name

    try:
        with open(tmp_path, "rb") as fh:
            file_obj = await client.files.create(file=fh, purpose="batch")
    finally:
        import os

        try:
            os.unlink(tmp_path)
        except OSError:
            pass

    batch = await client.batches.create(
        input_file_id=file_obj.id,
        endpoint="/v1/chat/completions",
        completion_window="24h",
    )
    logger.info("openai_batch: submitted %d requests — batch_id=%s", len(requests), batch.id)
    return batch.id


async def collect(handle: str) -> list[BatchResult] | BatchPending:
    """Poll an OpenAI batch by id.  Returns results or BatchPending."""
    import openai

    from backend.ai.cost_tracker import record as _record
    from backend.db.model_config_store import ModelConfigStore, provider_label_for

    cfg = ModelConfigStore()
    inst = cfg.resolved()
    api_key = inst.get("api_key") or ""
    base_url = inst.get("base_url") or ""

    client = openai.AsyncOpenAI(api_key=api_key or None, base_url=base_url or None)

    batch = await client.batches.retrieve(handle)
    logger.debug("openai_batch: poll %s → %s", handle, batch.status)

    if batch.status in ("validating", "in_progress", "finalizing"):
        return BatchPending()

    if batch.status in _FAILED_STATES:
        logger.error("openai_batch: batch %s terminal status=%s", handle, batch.status)
        return []

    if not batch.output_file_id:
        return []

    file_content = await client.files.content(batch.output_file_id)
    raw_text = file_content.text

    results: list[BatchResult] = []
    for line in raw_text.strip().splitlines():
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        custom_id = item.get("custom_id", "")
        response = item.get("response") or {}
        if response.get("status_code") == 200:
            body = response.get("body") or {}
            text, finish_reason, usage = extract_chat_result(body)
            _record(
                "batch",
                body.get("model", ""),
                usage.get("prompt_tokens", 0),
                usage.get("completion_tokens", 0),
                batch=True,
                provider=provider_label_for("openai_compatible", base_url),
            )
            results.append(BatchResult(custom_id=custom_id, text=text, stop_reason=finish_reason))
        else:
            err_body = item.get("error") or {}
            err = err_body.get("message") or "unknown error"
            logger.warning("openai_batch: item %s failed: %s", custom_id, err)
            results.append(BatchResult(custom_id=custom_id, text=None, error=err))

    logger.info("openai_batch: collected %d results from batch %s", len(results), handle)
    return results
