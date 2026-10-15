"""In-flight connector turn registry — lets a ``/stop`` message cancel a running turn.

WHY (real incident, 2026-07-20): a costly message ("return all my July transactions") ran
uninterruptibly on Telegram — the bot's handler was busy pumping the response and nothing the
user sent could stop it. The comparable projects hit the SAME wall (OpenClaw #20600/#27332/
#51046, Hermes #26813/#22176): the hard part isn't the command, it's making a stop message
REACH a running turn and tear down the LLM call, not just hide output.

Design (informed by those projects' open issues):
  * Keyed by ``(connector, channel_id)`` — one conversation. Tracks the ACTIVE turn's asyncio
    task so a control message on the SAME channel can cancel it.
  * Also tracks any PENDING debounce/flush task (Telegram batches split fragments before the
    turn starts) so a stop during the debounce window cancels the not-yet-launched run too
    (OpenClaw #51046 — "/stop should cancel pending debounce buffer").

Cancelling the turn task raises ``CancelledError`` into ``assistant.run``, whose ``finally``
``aclose()``s the turn generator → the provider stream is explicitly torn down (see
``openai_provider``/``anthropic_provider`` stream_chat), so generation AND billing stop — a
HARDER cancel than the cooperative poll-between-tools flag OpenClaw/Hermes use, which can't
stop a single long generation (exactly the runaway-dump failure mode).

KNOWN GAP (documented, not guarded — matches OpenClaw #27332): the microsecond window between
a debounce task spawning a turn and that turn registering itself as active. A ``/stop`` landing
exactly there finds nothing to cancel; the turn runs. Re-sending ``/stop`` cancels it once it
has registered. Not worth a fragile abort-flag to close (a flag correct for "drain the queued
run" also wrongly skips a fresh message sent right after the stop).

Process-local, best-effort, never raises into a caller (a stop is a courtesy, not a
guarantee). Not persisted: a restart clears it, which is correct — there's no turn to stop.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Dict, Optional, Set, Tuple

log = logging.getLogger(__name__)

Key = Tuple[str, str]

# (connector, channel) → the asyncio Task running assistant.run for that conversation.
_active: Dict[Key, asyncio.Task] = {}
# session_id → that same task. A SECOND index, not a replacement: `/stop` is a channel
# gesture ("stop what you're doing here"), while mid-turn injection is a SESSION question
# ("is there a turn to splice this into?"). They are not the same key — an untrusted shared
# channel keys sessions PER SENDER (`chat.run.session_key_for`), so one channel can hold
# several concurrent sessions. Routing injection on the channel key would queue sender B's
# message onto sender A's session, where nothing would ever drain it.
_active_by_session: Dict[str, asyncio.Task] = {}
# (connector, channel) → debounce/flush tasks that will LAUNCH a turn but haven't yet.
_debounce: Dict[Key, Set[asyncio.Task]] = {}


def _key(connector: Optional[str], channel_id: str) -> Key:
    return (str(connector or "").lower(), str(channel_id))


def register_active(
    connector: Optional[str],
    channel_id: str,
    task: asyncio.Task,
    *,
    session_id: Optional[str] = None,
) -> None:
    """Record the task running the turn for this channel (called at the turn's start).

    ``session_id`` additionally indexes it for the injection router — see
    ``is_running_for_session``. Optional so a caller that only needs ``/stop`` semantics
    (or a test) is unaffected."""
    _active[_key(connector, channel_id)] = task
    if session_id:
        _active_by_session[str(session_id)] = task


def clear_active(
    connector: Optional[str],
    channel_id: str,
    task: asyncio.Task,
    *,
    session_id: Optional[str] = None,
) -> None:
    """Drop the active task IF it's still the registered one (never clobber a newer turn)."""
    k = _key(connector, channel_id)
    if _active.get(k) is task:
        _active.pop(k, None)
    if session_id and _active_by_session.get(str(session_id)) is task:
        _active_by_session.pop(str(session_id), None)


def register_debounce(connector: Optional[str], channel_id: str, task: asyncio.Task) -> None:
    """Track a pending debounce/flush task so a stop cancels the not-yet-launched run too."""
    k = _key(connector, channel_id)
    live = {t for t in _debounce.get(k, set()) if not t.done()}
    live.add(task)
    _debounce[k] = live


def clear_debounce(connector: Optional[str], channel_id: str, task: asyncio.Task) -> None:
    k = _key(connector, channel_id)
    live = _debounce.get(k)
    if live is not None:
        live.discard(task)
        if not live:
            _debounce.pop(k, None)


def is_running(connector: Optional[str], channel_id: str) -> bool:
    t = _active.get(_key(connector, channel_id))
    return t is not None and not t.done()


def is_running_for_session(session_id: str) -> bool:
    """Whether a connector turn is CURRENTLY executing for ``session_id``.

    The connector twin of ``ai.turns.executing_turn_id`` — the routing authority for
    mid-turn injection. Answered from the task itself (popped the instant it finishes),
    never from ``live_sessions.is_live()``, which also reads live-ish around a turn that
    merely PARKED for an approval decision; injecting there would leave the message with
    nothing to drain it (security review finding 7a, and the same reasoning `api/chat.py`
    records for the web path)."""
    t = _active_by_session.get(str(session_id or ""))
    return t is not None and not t.done()


def stop(connector: Optional[str], channel_id: str) -> bool:
    """Cancel this channel's in-flight turn AND any pending debounce flush. Returns True iff
    something was actually cancelled (so the caller can say "Stopped." vs. "Nothing to stop.")."""
    k = _key(connector, channel_id)
    cancelled = False
    for t in list(_debounce.get(k, set())):
        if not t.done():
            t.cancel()
            cancelled = True
    _debounce.pop(k, None)
    active = _active.get(k)
    if active is not None and not active.done():
        active.cancel()
        cancelled = True
    return cancelled


def reset() -> None:
    """Test hook — clear all state."""
    _active.clear()
    _active_by_session.clear()
    _debounce.clear()
