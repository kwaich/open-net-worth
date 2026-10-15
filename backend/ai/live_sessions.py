"""Fan a server-driven turn's event stream out to whoever is watching it.

An Automation run — and, since the concurrent-conversations RFC Phase 2/3, every
interactive and resolve turn too — is driven by a task no browser request coroutine owns.
This is the wire that connects a watching chat panel to it: the driver publishes every
event it already produces, a panel watching that session subscribes and gets the SAME
events the interactive path renders — ``text_delta`` by ``text_delta``. Not polling, not a
summary: the identical stream.

Deliberately in-process and ephemeral:

* **No subscriber, no cost.** ``publish`` is a dict lookup that returns immediately when
  nobody is watching, which is the normal case for a scheduled run at 3am.
* **A slow subscriber is dropped, never back-pressures the turn.** Its queue is bounded;
  once full the events are discarded for that watcher alone. A watcher that missed events
  still ends correct — the transcript is the durable record and the panel reconciles
  against it. A turn that stalled because a browser tab froze would be a real bug.
* **The replay buffer covers the join gap, plus a short post-end grace window.** A
  subscriber that attaches mid-turn gets the events of the turn so far, so its text does
  not start from the middle of a sentence. A subscriber that attaches in the few hundred
  milliseconds around a turn ENDING — the gap between a 202 response and the client's own
  ``GET /live``, or a tab reopened moments after a turn finished — still gets the full
  replay instead of racing the buffer's teardown (Phase 3, ``_REPLAY_GRACE_S``).
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import AsyncIterator

logger = logging.getLogger(__name__)

# Per-watcher queue depth. A fast turn emits a text_delta per token; this is a couple of
# seconds of head start for a watcher whose event loop is busy rendering.
_QUEUE_MAX = 2048

# Events retained per live session so a panel that opens mid-turn can catch up on the
# current turn rather than joining mid-sentence.
_REPLAY_MAX = 4096

# How long a finished turn's replay buffer survives past its own end() — long enough to
# absorb the 202-response-to-GET/live attach gap and a tab reopened moments after a short
# turn completed, short enough that a stale buffer is never mistaken for "still running"
# (is_live()/live_session_ids() are keyed off _episodes, never off replay presence, so the
# grace window never makes a finished turn LOOK live — see is_live()'s docstring).
_REPLAY_GRACE_S = 30.0

_subscribers: dict[str, set[asyncio.Queue]] = {}
_replay: dict[str, list[dict]] = {}
_replay_closed_at: dict[str, float] = {}

# The turn ids currently open per session — the authority for "is a turn running", never
# the replay dict (which can outlive the turn by _REPLAY_GRACE_S). Keyed by (session_id,
# turn_id) rather than a bare counter: two turns can genuinely overlap on one session (an
# Automation run mid-turn while the user posts a followup into the same conversation), and
# a set makes end() idempotent and self-correcting per turn_id instead of a mismatched
# begin/end pair silently corrupting a SIBLING turn's count (security review, 2026-08-06).
_episodes: dict[str, set[str]] = {}


def _prune_expired() -> None:
    if not _replay_closed_at:
        return
    now = time.monotonic()
    expired = [
        sid for sid, closed_at in _replay_closed_at.items() if now - closed_at > _REPLAY_GRACE_S
    ]
    for sid in expired:
        _replay_closed_at.pop(sid, None)
        _replay.pop(sid, None)


def publish(session_id: str, event: dict) -> None:
    """Hand one event to every watcher of this session. Never raises, never blocks."""
    if not session_id:
        return
    buffer = _replay.get(session_id)
    if buffer is not None:
        buffer.append(event)
        if len(buffer) > _REPLAY_MAX:
            del buffer[: len(buffer) - _REPLAY_MAX]
    for queue in list(_subscribers.get(session_id) or ()):
        try:
            queue.put_nowait(event)
        except asyncio.QueueFull:
            # This watcher is behind. Drop ITS event; the turn and every other watcher
            # carry on. The transcript remains the durable record it reconciles against.
            logger.debug("live session watcher fell behind: %s", session_id)


def begin(session_id: str, turn_id: str) -> None:
    """Mark one turn live under this session.

    Starts a FRESH replay buffer only when no episode is currently open — an overlapping
    ``begin()`` for a SIBLING turn must never wipe events a watcher has already been
    served, and a turn starting after a PRIOR turn's grace-retained buffer has expired (or
    is still draining) must not inherit that unrelated turn's tail.
    """
    if not session_id or not turn_id:
        return
    _prune_expired()
    open_ids = _episodes.setdefault(session_id, set())
    if not open_ids:
        _replay[session_id] = []
        _replay_closed_at.pop(session_id, None)
    open_ids.add(turn_id)


def end(session_id: str, turn_id: str) -> None:
    """Close one turn's bracket.

    A no-op unless THIS ``turn_id`` currently owns an open bracket on this session — a
    stale, duplicate, or mismatched ``end()`` can therefore never end a SIBLING turn that
    is still writing. Only wakes watchers once every turn open on this session has ended;
    the replay buffer itself is RETAINED for ``_REPLAY_GRACE_S`` rather than dropped here
    (see ``_prune_expired``).
    """
    if not session_id or not turn_id:
        return
    open_ids = _episodes.get(session_id)
    if open_ids is None or turn_id not in open_ids:
        return
    open_ids.discard(turn_id)
    if open_ids:
        return
    _episodes.pop(session_id, None)
    _replay_closed_at[session_id] = time.monotonic()
    for queue in list(_subscribers.get(session_id) or ()):
        try:
            queue.put_nowait({"type": "stream_end"})
        except asyncio.QueueFull:
            # The terminal marker is mandatory: evict one ordinary queued event so a
            # watcher can always observe the end and reconcile its durable transcript.
            queue.get_nowait()
            queue.put_nowait({"type": "stream_end"})


def is_live(session_id: str) -> bool:
    """Is a turn CURRENTLY running — never "is a replay buffer still around".

    Keyed off ``_episodes`` (open brackets), not ``_replay`` (which is deliberately
    retained past a turn's end for the attach-race grace window) — otherwise a client
    reopening a tab moments after a short turn finished would see it reported "live"
    forever until the grace window happened to expire, which would also wrongly route a
    POST during that window into mid-turn injection instead of starting a fresh turn.
    """
    return bool(_episodes.get(session_id))


async def watch(session_id: str) -> AsyncIterator[dict]:
    """Yield this session's events until the turn ends or the caller disconnects.

    Yields the current turn's replay first, so a panel that opens mid-run renders what
    has happened so far before following along live — including a turn that finished just
    moments ago and is still inside its grace window, in which case this replays the whole
    thing and returns without ever reaching the live-queue wait.
    """
    _prune_expired()
    queue: asyncio.Queue = asyncio.Queue(maxsize=_QUEUE_MAX)
    # Register BEFORE reading the replay, and do not await in between: on a single event
    # loop that makes the handover gapless — anything published from here on lands in the
    # queue, and everything before it is already in the buffer.
    _subscribers.setdefault(session_id, set()).add(queue)
    try:
        for event in list(_replay.get(session_id) or ()):
            yield event
        if not is_live(session_id):
            return
        while True:
            event = await queue.get()
            if event.get("type") == "stream_end":
                return
            yield event
    finally:
        watchers = _subscribers.get(session_id)
        if watchers is not None:
            watchers.discard(queue)
            if not watchers:
                _subscribers.pop(session_id, None)


def _reset_for_tests() -> None:
    _subscribers.clear()
    _replay.clear()
    _replay_closed_at.clear()
    _episodes.clear()


def watcher_count(session_id: str) -> int:
    return len(_subscribers.get(session_id) or ())


def live_session_ids() -> list[str]:
    return sorted(_episodes)


__all__ = [
    "begin",
    "end",
    "is_live",
    "live_session_ids",
    "publish",
    "watch",
    "watcher_count",
]
