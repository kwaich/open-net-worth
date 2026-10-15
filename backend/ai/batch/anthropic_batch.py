"""Anthropic native Message Batches API — batch submit + collect hooks.

Registered on ``core.llm_seam`` at startup when the active transport is
``anthropic``.  Falls back to the seam's sync fallback when the API key is
absent or the provider is disabled.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from backend.core.errors import ConfigurationError
from backend.core.llm_seam import BatchPending, BatchRequest, BatchResult

logger = logging.getLogger(__name__)

# Anthropic batch states that indicate terminal failure.
_FAILED_STATES = {"errored", "canceled", "expired"}


def _extract_text(content: list[Any]) -> str:
    """Extract text from a message content block list.

    Handles both text blocks (plain completion) and tool_use blocks (structured
    tool_choice responses — serialize the input as JSON so callers can parse it).
    """
    for block in content or []:
        block_type = getattr(block, "type", None)
        if block_type == "text":
            return block.text or ""
        if block_type == "tool_use":
            return json.dumps(getattr(block, "input", {}))
    return ""


async def submit(task: str, requests: list[BatchRequest]) -> str:
    """Submit a list of BatchRequest items to the Anthropic Message Batches API.

    Returns the batch id (opaque string) as the handle.
    """
    import anthropic

    from backend.db.provider_keys import effective_api_key

    api_key = effective_api_key("anthropic")
    client = anthropic.AsyncAnthropic(api_key=api_key)

    batch_requests = []
    for req in requests:
        if not req.model:
            raise ConfigurationError(
                f"Batch request {req.custom_id!r} has no model; assign one before submission."
            )
        params: dict = {
            "model": req.model,
            "max_tokens": req.max_tokens,
            "messages": [{"role": "user", "content": req.prompt}],
        }
        if req.system:
            params["system"] = req.system
        if req.output_schema is not None:
            params["tools"] = [
                {
                    "name": req.output_schema.name,
                    "description": req.output_schema.description,
                    "input_schema": req.output_schema.schema,
                }
            ]
            params["tool_choice"] = {"type": "tool", "name": req.output_schema.name}
        params.update(req.extra_params or {})
        batch_requests.append(
            {
                "custom_id": req.custom_id,
                "params": params,
            }
        )

    batch = await client.messages.batches.create(requests=batch_requests)
    logger.info("anthropic_batch: submitted %d requests — batch_id=%s", len(requests), batch.id)
    return batch.id


async def collect(handle: str) -> list[BatchResult] | BatchPending:
    """Poll an Anthropic batch by id.  Returns results or BatchPending."""
    import anthropic

    from backend.ai.cost_tracker import record as _record
    from backend.db.model_config_store import provider_label_for
    from backend.db.provider_keys import effective_api_key

    api_key = effective_api_key("anthropic")
    client = anthropic.AsyncAnthropic(api_key=api_key)

    batch = await client.messages.batches.retrieve(handle)
    logger.debug("anthropic_batch: poll %s → %s", handle, batch.processing_status)

    if batch.processing_status == "in_progress":
        return BatchPending()

    if batch.processing_status in _FAILED_STATES:
        logger.error("anthropic_batch: batch %s terminal state=%s", handle, batch.processing_status)
        return []

    results: list[BatchResult] = []
    async for item in await client.messages.batches.results(handle):
        if item.result.type == "succeeded":
            msg = item.result.message
            text = _extract_text(msg.content)
            usage = msg.usage
            _record(
                "batch",
                msg.model,
                usage.input_tokens,
                usage.output_tokens,
                getattr(usage, "cache_creation_input_tokens", 0) or 0,
                getattr(usage, "cache_read_input_tokens", 0) or 0,
                batch=True,
                provider=provider_label_for("anthropic", ""),
            )
            results.append(
                BatchResult(custom_id=item.custom_id, text=text, stop_reason=msg.stop_reason)
            )
        else:
            err = str(getattr(item.result, "error", "unknown error"))
            logger.warning("anthropic_batch: item %s failed: %s", item.custom_id, err)
            results.append(BatchResult(custom_id=item.custom_id, text=None, error=err))

    logger.info("anthropic_batch: collected %d results from batch %s", len(results), handle)
    return results
