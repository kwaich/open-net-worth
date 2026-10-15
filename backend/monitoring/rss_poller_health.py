"""Hot-tier per-source health/politeness state — split out of rss_poller_hot.py to keep that
module under the file-size convention (mirrors the rss_poller_topics.py precedent). Owns two
related but distinct concerns, both module-level process state since the hot poller is a single
long-lived loop:

  * **failure-streak backoff** (SF-3, INT8 code review): after `_FAILURE_STREAK_THRESHOLD`
    consecutive failures a source is skipped for an exponentially growing number of ticks
    (capped `_FAILURE_BACKOFF_MAX_TICKS`), logging ONCE on outage and ONCE on recovery;
  * **per-row min_interval_sec politeness** (INT9 Phase B): a source is due once enough hot
    ticks — not wall-clock seconds — have elapsed since its last attempt, so a row slower than
    the shared ~90s tick cadence (GDELT; a "slow"-cadence custom firehose) polls proportionally
    less often without needing its own scheduler job. TICK-counted rather than wall-clock so
    this behaves identically whether ticks are 90s apart in production or fired back-to-back in
    a test — no clock mocking needed.

Pure orchestration bookkeeping — no I/O beyond `logging`. `rss_poller_hot.py` calls
`record_tick()` once per `run_hot_tick()`, then every fetch loop (`_run_feed_registry`,
`_run_gdelt`, `_run_custom_sources`) consults `_should_skip`/`_due_for_interval` before
fetching and reports back via `_record_result`/`_mark_attempted`.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Mirrors scheduler._HOT_INTERVAL_SECONDS as a local literal, not an import (backend.scheduler
# already imports monitoring.rss_poller — the reverse would cycle). Translates a row's
# min_interval_sec into a TICK count (see _due_for_interval).
_HOT_TICK_INTERVAL_SEC = 90

_FAILURE_STREAK_THRESHOLD = 3  # consecutive failures before backing off
_FAILURE_BACKOFF_BASE_TICKS = 2  # first backoff: skip this many ticks
_FAILURE_BACKOFF_MAX_TICKS = 10  # cap (~15 min at the 90s hot cadence)

# source_key (a MarketFeed's `via`, or "api:gdelt") -> health state. Module-level: the hot
# tier's poll loop is a single long-lived process, so this persists across ticks by design.
_source_health: dict[str, dict] = {}

# INT9 Phase B: per-row min_interval_sec politeness, TICK-counted (see module docstring).
# `_hot_tick_counter` advances once per run_hot_tick() call (via record_tick()); `_last_attempt_
# tick` remembers which tick each source key was last fetched on.
_hot_tick_counter = 0
_last_attempt_tick: dict[str, int] = {}


def record_tick() -> None:
    """Advance the shared tick counter. Called ONCE per `run_hot_tick()`, before any fetch —
    everything else in this module reads `_hot_tick_counter` as "the current tick"."""
    global _hot_tick_counter
    _hot_tick_counter += 1


def _source_state(key: str) -> dict:
    return _source_health.setdefault(
        key, {"failures": 0, "skip_remaining": 0, "outage_logged": False}
    )


def _due_for_interval(key: str, min_interval_sec: int) -> bool:
    """True if `key` was never attempted, or `min_interval_sec` worth of hot ticks (rounded UP —
    300s at a 90s tick needs 4, not 3.33) have elapsed since its last attempt. Tick-counted, not
    wall-clock, so a test's back-to-back calls behave like production's real ~90s-apart ticks."""
    ticks_needed = max(1, -(-min_interval_sec // _HOT_TICK_INTERVAL_SEC))  # ceil division
    last = _last_attempt_tick.get(key)
    if last is None:
        return True
    return (_hot_tick_counter - last) >= ticks_needed


def _mark_attempted(key: str) -> None:
    _last_attempt_tick[key] = _hot_tick_counter


def source_health_snapshot() -> dict[str, dict]:
    """Read-only snapshot of each source's failure-streak/backoff state, keyed by `via` (INT9
    Phase B's health join, RFC §3). No recorded state (never attempted, or not ticked since
    boot) means ABSENT — treat a missing key as "unknown", not "confirmed zero failures". Only
    hot-tier-fetched sources ever appear (a fast/slow row has no entry). A fresh copy — mutating
    it never touches the poller's live state."""
    return {
        via: {
            "consecutive_failures": state["failures"],
            "backing_off": state["skip_remaining"] > 0,
            "skip_remaining_ticks": state["skip_remaining"],
        }
        for via, state in _source_health.items()
    }


def _should_skip(key: str) -> bool:
    """True (and decrements the counter) if `key` is currently in its backoff window."""
    state = _source_state(key)
    if state["skip_remaining"] > 0:
        state["skip_remaining"] -= 1
        return True
    return False


def _record_result(key: str, *, ok: bool) -> None:
    """Update `key`'s failure streak and (re)compute its backoff window. Logs ONCE when a
    source crosses into an outage and ONCE when it recovers — never per-tick."""
    state = _source_state(key)
    if ok:
        if state["outage_logged"]:
            logger.warning("rss_poller_hot: source %s recovered", key)
        state["failures"] = 0
        state["skip_remaining"] = 0
        state["outage_logged"] = False
        return

    state["failures"] += 1
    if state["failures"] < _FAILURE_STREAK_THRESHOLD:
        return
    streak_over_threshold = state["failures"] - _FAILURE_STREAK_THRESHOLD
    ticks = min(
        _FAILURE_BACKOFF_MAX_TICKS,
        _FAILURE_BACKOFF_BASE_TICKS * (2**streak_over_threshold),
    )
    state["skip_remaining"] = ticks
    if not state["outage_logged"]:
        logger.warning(
            "rss_poller_hot: source %s failing (%d consecutive) — backing off %d tick(s)",
            key,
            state["failures"],
            ticks,
        )
        state["outage_logged"] = True
