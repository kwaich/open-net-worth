"""In-memory, TTL-bounded, single-use staging for an in-flight Lunch Flow OAuth authorization.

Same shape and reasoning as ``backend/mcp/oauth_pending.py`` (the MCP OAuth flow's own
staging module) and, one layer further back, ``services/shortcuts/tickets.py``: a live
``state`` is worthless once the flow finishes or expires, so surviving a restart buys
nothing, and a restart invalidating an in-flight sign-in is the safe direction. Deliberately
NOT a table — a ``SECRET_TABLES`` row holding an about-to-be-exchanged authorization code
would be a worse artefact than a lost redirect after a crash.

Lunch Flow's flow is a plain OAuth 2.0 authorization-code redirect — no DCR, no PKCE, no
per-server multiplexing (this app holds exactly ONE Lunch Flow developer credential pair, not
a registry of them like the MCP client store) — so this is deliberately a much smaller module
than ``oauth_pending.py``: one config, one flow at a time in practice, nothing to resolve at
completion beyond the ``redirect_uri`` the exchange must echo back.

Two separate maps, both keyed by ``state``:

  * ``_pending`` — the staged flow itself (just the ``redirect_uri`` the token exchange must
    send back, since it has to match what the authorize call sent). ``pop()`` is single-use: a
    replayed ``state`` gets nothing the second time — the state IS the flow's credential (same
    reasoning as the MCP OAuth ADR ``2026-08-16-mcp-oauth-callback-is-auth-exempt-state-is-the-
    credential.md``, which this connector deliberately mirrors).
  * ``_status`` — a short, TOKEN-FREE record of how a popped flow finished (``ok``/``error``),
    written by ``lunch_flow_oauth.complete`` right after it pops the entry above. This is what
    ``GET /api/lunch-flow/oauth/{flow_id}`` (``flow_id`` IS ``state``) reads while polling — it
    must still answer once the flow has been popped, or the poll would see "unknown" the
    instant the popup's callback beat it to a poll tick. NEVER holds a token.
"""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass
from typing import Dict, Optional

TTL_SECONDS = 600  # 10 minutes — matches the MCP OAuth flow's own window.
# A completed flow's outcome must still be readable by a poll that lands a moment after the
# popup's own postMessage already told the opener.
STATUS_TTL_SECONDS = 900

# One operator running at most a handful of concurrent connect/reconnect attempts is the whole
# use case; bounded so neither map can grow without limit.
MAX_OUTSTANDING = 16
MAX_STATUS = 32


@dataclass(frozen=True)
class PendingFlow:
    state: str
    redirect_uri: str
    expires_at: float


@dataclass(frozen=True)
class _StatusEntry:
    status: str  # "ok" | "error"
    error: Optional[str]
    recorded_at: float


_pending: Dict[str, PendingFlow] = {}
_status: Dict[str, _StatusEntry] = {}


def _prune_pending(now: float) -> None:
    for state in [s for s, f in _pending.items() if f.expires_at <= now]:
        _pending.pop(state, None)


def _prune_status(now: float) -> None:
    cutoff = now - STATUS_TTL_SECONDS
    for state in [s for s, e in _status.items() if e.recorded_at <= cutoff]:
        _status.pop(state, None)


def stage(*, redirect_uri: str, now: Optional[float] = None) -> str:
    """Stage one authorization attempt; return the freshly minted ``state`` (256-bit,
    server-minted, single-use — this IS the flow's credential)."""
    current = time.time() if now is None else now
    _prune_pending(current)
    if len(_pending) >= MAX_OUTSTANDING:
        oldest = min(_pending.values(), key=lambda f: f.expires_at)
        _pending.pop(oldest.state, None)

    state = secrets.token_urlsafe(32)
    _pending[state] = PendingFlow(
        state=state, redirect_uri=redirect_uri, expires_at=current + TTL_SECONDS
    )
    return state


def pop(state: str, *, now: Optional[float] = None) -> Optional[PendingFlow]:
    """Return + DELETE the staged flow for ``state`` (single-use), or ``None`` if unknown or
    expired. A caller MUST NOT distinguish these two cases in anything shown to an
    unauthenticated caller — the callback route answers the same fixed page either way."""
    current = time.time() if now is None else now
    _prune_pending(current)
    flow = _pending.pop(state or "", None)
    if flow is None or flow.expires_at <= current:
        return None
    return flow


def record_status(
    state: str, status: str, error: Optional[str], *, now: Optional[float] = None
) -> None:
    """Record how a popped flow finished. Called exactly once per flow, right after ``pop()``
    in ``lunch_flow_oauth.complete`` — never holds a token, only a status + a human-readable
    error."""
    current = time.time() if now is None else now
    _prune_status(current)
    if len(_status) >= MAX_STATUS:
        oldest_state = min(_status, key=lambda s: _status[s].recorded_at)
        _status.pop(oldest_state, None)
    _status[state] = _StatusEntry(status=status, error=error, recorded_at=current)


def get_status(state: str, *, now: Optional[float] = None) -> dict:
    """``{"status": "pending"|"ok"|"error", "error": str|None}`` — NEVER a token.

    A flow still staged (nobody has completed it yet) reads ``pending``. A popped flow reads
    whatever ``record_status`` recorded. A ``state`` this process has never heard of, or one
    that expired before anyone ever completed it, reads as a generic ``error``."""
    current = time.time() if now is None else now
    _prune_pending(current)
    _prune_status(current)
    if state in _pending:
        return {"status": "pending", "error": None}
    entry = _status.get(state or "")
    if entry is None:
        return {"status": "error", "error": "This sign-in attempt has expired or is unknown."}
    return {"status": entry.status, "error": entry.error}


def _reset_for_tests() -> None:
    _pending.clear()
    _status.clear()
