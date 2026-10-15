"""ContextVar carrying the model actually serving the CURRENT turn.

``ModelConfigStore`` knows the INSTANCE DEFAULT (Settings → AI Model). But a turn
may run on a different model via a per-conversation composer pick, a per-agent
model, or AMS auto-routing (e.g. the default is Claude Sonnet while THIS turn runs
on ``xai:grok-4.5``). The entrypoint (``assistant.run``) resolves the per-turn model
+ provider once and stamps it here, so introspection tools (``get_model_config``)
report what is TRULY running instead of the default — otherwise "which model are
you?" answers the configured default, not the active one.

Leaf module (stdlib ``contextvars`` only) so ``db``/``ai`` can both read it without a
layering violation. NEVER carries the api_key — non-secret identity only.
"""

from __future__ import annotations

from contextvars import ContextVar
from typing import Optional

# {model, provider_key, provider_label, transport, base_url} — or None outside a turn.
_active_model: ContextVar[Optional[dict]] = ContextVar("active_model", default=None)


def set_active_model(info: Optional[dict]) -> None:
    """Record the model resolved for the current turn (called by the entrypoint)."""
    _active_model.set(info)


def get_active_model() -> Optional[dict]:
    """The current turn's resolved model info, or None when not inside a turn."""
    return _active_model.get()
