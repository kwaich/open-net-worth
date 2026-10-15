"""Batch-adapter capability routing.

The July-9 2026 incident (owner-diagnosed): the dispatcher routed EVERY
``openai_compatible`` transport straight to ``openai_batch`` regardless of which
actual endpoint it was — when the owner switched to xAI (``openai_compatible`` +
``base_url=https://api.x.ai/v1``), every batch submit failed because xAI's
``/v1/batches`` is not OpenAI-shape (it requires ``name``, refuses
``endpoint``/``completion_window``, and serves results via a different route).
``openai_compatible`` is a TRANSPORT LABEL, not a batch-API shape — it covers
OpenAI itself plus a long tail of aggregators/proxies/local runtimes (Gemini,
Ollama, GLM, OpenRouter, LiteLLM, a bespoke gateway, ...) that mostly do NOT
implement a batch endpoint at all, let alone the same one.

This module resolves the (transport, base_url) pair to the adapter that actually
matches the endpoint's real capability — never guessing a wire format for an
endpoint we haven't verified. Anything unrecognized routes to ``"sync"`` (the
seam's per-request fallback): batch is a cost optimization, never a correctness
dependency, so an unknown endpoint must degrade gracefully rather than fail loudly
like the incident did.

Pure / stdlib-only so it's trivially unit-testable without mocking HTTP or config.
"""

from __future__ import annotations

from urllib.parse import urlparse

# Hosts with a CONFIRMED, adapter-implemented batch API. Anything else on the
# openai_compatible transport falls back to sync — see the module docstring.
_OPENAI_HOST = "api.openai.com"
_XAI_HOST = "api.x.ai"

SYNC_ADAPTER_KEY = "sync"


def resolve_adapter_key(transport: str, base_url: str) -> str:
    """Return the adapter key for a resolved (transport, base_url) pair.

    One of ``"anthropic"``, ``"openai"``, ``"xai"``, or ``"sync"`` (the fallback —
    NEVER an exception; an unrecognized pair always degrades gracefully).
    """
    if transport == "anthropic":
        return "anthropic"
    if transport == "openai_compatible":
        host = _hostname(base_url)
        if host == _OPENAI_HOST:
            return "openai"
        if host == _XAI_HOST:
            return "xai"
        return SYNC_ADAPTER_KEY
    # gemini/ollama/other transports are ALSO surfaced via "openai_compatible" in
    # this codebase's catalog (see db.model_config_store.PROVIDER_CATALOG) — any
    # transport string that isn't "anthropic"/"openai_compatible" is unrecognized.
    return SYNC_ADAPTER_KEY


def _hostname(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower()
    except ValueError:
        return ""
