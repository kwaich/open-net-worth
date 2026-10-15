"""Proposed-change push seam — fan an agent-surfaced proposed change out to active
chat channels so the user can confirm it in-channel (mirrors ``core.channels`` for text).

The inversion pattern is the same: each connector module registers itself at import
time; a service calls ``push_proposed_change`` through the registry without
ever importing ``connectors`` (import-linter Contract 1 stays green: services → core,
connectors → core — never services → connectors).

A push fn receives ``(finding_id, proposed_change, summary)`` — the finding_id is used
as the change_id so a chat confirm tap routes back to the same record. Push fns are
**async** and must never raise (best-effort); a failing push is logged and dropped.
"""

from __future__ import annotations

import logging
from typing import Awaitable, Callable

log = logging.getLogger("backend.core.change_push")

# name → async push fn. fn(finding_id, proposed_change, summary) -> dict
PushFn = Callable[[str, dict, str], Awaitable[dict]]

_PUSH_FNS: dict[str, PushFn] = {}


def register_change_push(name: str, fn: PushFn) -> None:
    """Register an async proposed-change push fn under ``name``. Last registration wins."""
    _PUSH_FNS[name] = fn


def unregister_change_push(name: str) -> None:
    """Remove a push fn (used by tests to keep registrations isolated)."""
    _PUSH_FNS.pop(name, None)


def registered_push_fns() -> dict[str, PushFn]:
    """A snapshot copy of the currently-registered push fns (name → async fn)."""
    return dict(_PUSH_FNS)


async def push_proposed_change(finding_id: str, proposed_change: dict, summary: str) -> dict:
    """Fan a proposed change out to all registered chat channels. Best-effort: a
    failing channel is logged and skipped — never raises, never blocks the caller.

    Returns a dict of ``{channel_name: result}`` for the channels that were attempted."""
    results: dict = {}
    for name, fn in list(_PUSH_FNS.items()):
        try:
            results[name] = await fn(finding_id, proposed_change, summary)
        except Exception as exc:  # pragma: no cover - defensive; each fn should not raise
            log.warning("change push to '%s' failed: %s", name, exc)
            results[name] = {"error": str(exc)}
    return results
