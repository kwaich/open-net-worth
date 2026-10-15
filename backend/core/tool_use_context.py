"""Per-dispatch tool_use id — a ContextVar leaf (RFC 2026-07-22 approval-as-pending-tool_use).

``ai.tools.registry.dispatch`` sets the id of the ``tool_use`` block currently being
dispatched here, BEFORE calling the handler, so a gated write's staging code
(``ai.tools._confirm_gate.stage``/``stage_generic``) can key its ``PendingPlanStore`` entry
on the SAME id the turn loop will later need to resolve — without threading a new
``tool_use_id`` parameter through every one of the ~270 registered handler signatures.
Mirrors the ``core.chat_session`` / ``core.audit_context`` ContextVar seams.

``None`` outside a dispatch (no active tool call), or when the caller genuinely has no
tool_use to bind to (a bare ``execute_tool(name, args)`` from MCP/connectors/tests/REST —
see ``registry.dispatch``'s ``tool_use_id=`` default). Reading it off any of those paths is
a pure no-op: the confirm gate's ``should_gate`` already refuses to gate a call with no
id available (nothing to key a resolvable pending entry on), so those callers simply never
pause — the existing ungated behavior on a surface this mechanism has not been taught about.

Imports nothing internal — a leaf, so it can never create an import cycle.
"""

from __future__ import annotations

from contextvars import ContextVar
from typing import Optional

_tool_use_id: ContextVar[Optional[str]] = ContextVar("tool_use_id", default=None)


def set_tool_use_id(tool_use_id: Optional[str]) -> None:
    """Bind the ``tool_use_id`` of the call currently being dispatched."""
    _tool_use_id.set(tool_use_id)


def get_tool_use_id() -> Optional[str]:
    """The in-flight dispatch's ``tool_use_id``, or ``None`` outside a dispatch."""
    return _tool_use_id.get()
