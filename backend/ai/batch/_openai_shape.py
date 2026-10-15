"""Shared /v1/chat/completions request/response shape helpers.

``openai_batch`` (api.openai.com) and ``xai_batch`` (api.x.ai) submit the SAME
per-line JSONL body against the SAME chat-completions endpoint shape — only the
surrounding batch-job lifecycle (create/poll/fetch-results) differs between the two
providers' batch APIs. Factored out here so both adapters translate
``BatchRequest``/``BatchOutputSchema`` identically instead of duplicating (and
risking drifting) the same logic twice.
"""

from __future__ import annotations

from backend.core.llm_seam import BatchRequest


def build_chat_body(req: BatchRequest, default_model: str) -> dict:
    """Build a ``/v1/chat/completions`` request body for one ``BatchRequest``.

    Translates ``req.output_schema`` (when set) into a forced function-tool call —
    the OpenAI-shape mirror of ``anthropic_batch``'s forced ``tool_use``: the same
    neutral structured-output contract, translated to this transport's own wire
    format. ``extra_params`` is merged in last so a genuinely provider-matched
    caller can still add/override fields.
    """
    body: dict = {
        "model": req.model or default_model,
        "max_tokens": req.max_tokens,
        "messages": ([{"role": "system", "content": req.system}] if req.system else [])
        + [{"role": "user", "content": req.prompt}],
    }
    if req.output_schema is not None:
        body["tools"] = [
            {
                "type": "function",
                "function": {
                    "name": req.output_schema.name,
                    "description": req.output_schema.description,
                    "parameters": req.output_schema.schema,
                },
            }
        ]
        body["tool_choice"] = {"type": "function", "function": {"name": req.output_schema.name}}
    body.update(req.extra_params or {})
    return body


def extract_chat_text(message: dict) -> str:
    """Pull the structured-output text out of one chat-completion ``message`` object.

    A forced ``tool_choice`` response carries the JSON in
    ``tool_calls[0].function.arguments`` — already a JSON string, unlike Anthropic's
    ``tool_use`` block (a dict that must be re-encoded). A plain completion (no
    ``output_schema`` was set) carries it in ``content``.
    """
    tool_calls = message.get("tool_calls") or []
    if tool_calls:
        return (tool_calls[0].get("function") or {}).get("arguments") or ""
    return message.get("content") or ""


def extract_chat_result(body: dict) -> tuple[str, str | None, dict]:
    """Extract ``(text, stop_reason, usage)`` from a ``/v1/chat/completions`` response body."""
    choices = body.get("choices") or []
    if not choices:
        return "", None, (body.get("usage") or {})
    choice = choices[0]
    message = choice.get("message") or {}
    return extract_chat_text(message), choice.get("finish_reason"), (body.get("usage") or {})
