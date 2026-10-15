"""Cost-attribution surface seam.

Mirrors ``core.audit_context``: upper layers SET the surface before calling the
assistant, and ``_record_cost`` / ``cost_tracker.record`` READ it to stamp each
cost record.  Lives in ``core`` (a leaf) so it can be imported from ``ai``,
``api``, and ``connectors`` without creating a layering violation.

Surfaces:
  - ``'web'``              — SSE chat endpoint (api/chat.py)
  - ``'connector:<name>'`` — inbound connector turn (telegram / slack / discord / …)
  - ``'connector'``        — connector turn when channel name is unavailable
  - ``'scheduled'``        — scheduled prompt_lucius run (main.py runner)
  - ``None``               — unset (uninstrumented path); recorded as ``'chat'``
                             by the legacy _record_cost fallback.
"""

from __future__ import annotations

from contextvars import ContextVar
from typing import Optional

_surface: ContextVar[Optional[str]] = ContextVar("cost_surface", default=None)


def set_cost_surface(surface: str) -> None:
    """Bind the cost-attribution surface for the current async task."""
    _surface.set(surface)


def get_cost_surface() -> Optional[str]:
    """The active surface, or ``None`` when no entrypoint set one."""
    return _surface.get()


def clear_cost_surface() -> None:
    """Reset the surface (call in a ``finally`` at the entrypoint)."""
    _surface.set(None)
