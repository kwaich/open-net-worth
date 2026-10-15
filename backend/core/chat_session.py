"""Per-turn chat-session id — a ContextVar leaf (ad-hoc-code-execution RFC §4.2).

``assistant.run`` sets the active conversation's session id here for the duration of a
turn so a tool handler can scope a per-conversation decision to it WITHOUT the session id
being threaded through every ``execute_tool`` call. Mirrors the ``core.email_consumer`` /
``core.auto_apply_scope`` ContextVar seams.

Used to attribute a turn to its conversation — e.g. routing a confirm-card grant to the
Automation that owns the run. Default ``None`` (no active turn / a headless or background
path that never set it), so reading it off the chat path is a pure no-op.

Imports nothing internal — a leaf, so it can never create an import cycle.
"""

from __future__ import annotations

from contextvars import ContextVar
from typing import Optional

_session_id: ContextVar[Optional[str]] = ContextVar("chat_session_id", default=None)


def set_session_id(session_id: Optional[str]) -> None:
    """Bind the active conversation's session id for this turn."""
    _session_id.set(session_id)


def get_session_id() -> Optional[str]:
    """The active conversation's session id, or ``None`` when no turn set it."""
    return _session_id.get()


def clear_session_id() -> None:
    """Clear the binding at turn end so it never leaks into the next turn."""
    _session_id.set(None)
