"""Seam: WHY a turn was cancelled, for the layer that settles the durable record.

``ai/turns.py`` knows the reason — it is the thing doing the cancelling. The code that
settles an Automation's run row lives in ``services``, which must never import ``ai``, and
so had no way to ask. It hedged instead, telling the owner a stopped run ended "either
from Stop or by the server restarting" when the process had known which one all along
(live incident 2026-08-21 18:27:20, ``turn_cancel_requested reason=shutdown`` — a dev
reload — reported to the owner as a Stop they never pressed).

Keyed by session, not by turn: the settle site holds a run row, and a run row names a
session. Every cancel records BEFORE the ``CancelledError`` it causes can propagate, so a
reader always takes the freshest reason for that session rather than a stale one — an
Automation's session outlives any single run of it.
"""

from __future__ import annotations

from collections import OrderedDict
from typing import Optional

# Bounded: a reason is recorded for every cancel but only READ by a caller settling a
# durable row, so entries that nobody claims are normal and must not accumulate. 128 is
# far more than the number of turns that can be cancelled between two settles.
_MAX_ENTRIES = 128

_reasons: "OrderedDict[str, str]" = OrderedDict()


def record_cancel_reason(session_id: str, reason: str) -> None:
    """Record why this session's in-flight turn is being cancelled."""
    if not session_id or not reason:
        return
    _reasons.pop(session_id, None)
    _reasons[session_id] = reason
    while len(_reasons) > _MAX_ENTRIES:
        _reasons.popitem(last=False)


def peek_cancel_reason(session_id: str) -> Optional[str]:
    """The reason, left in place — for a reader that is not the one settling the record."""
    return _reasons.get(session_id) if session_id else None


def take_cancel_reason(session_id: str) -> Optional[str]:
    """The reason, consumed — for the reader that settles the durable record with it."""
    return _reasons.pop(session_id, None) if session_id else None


def clear_cancel_reasons() -> None:
    """Test seam: drop everything recorded so far."""
    _reasons.clear()
