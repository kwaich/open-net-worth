"""monitoring/spike_detection.py — shared spike math, including the INT5 arrival-clock
addition (news-ingestion-latency.md §4).

No dedicated test file existed for this module despite it being split out in INT3 (rss_poller's
own tests cover it only indirectly through the poller). This file covers the primitives
directly, PLUS the July-14 Uber/ONWAI replay fixture: a late-indexed wave whose
published_at predates the recent window (so the publish-clock check misses it) but whose
first_seen is recent against an established history (so the arrival-clock check catches it).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from backend.monitoring.spike_detection import (
    _SPIKE_BASELINE_DAYS,
    _SPIKE_MIN_ABS,
    _SPIKE_MIN_HISTORY_DAYS,
    _SPIKE_WINDOW_SEC,
    _is_spike,
    _is_spike_any_clock,
)


def _iso_ago(seconds: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat()


def _iso_days_ago(days: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def _established_baseline(n_per_day: int, n_days: int) -> list[dict]:
    """A subject with n_per_day articles/day over n_days, BOTH published_at and first_seen
    correctly spread across history (a normally-functioning feed, not a cold start)."""
    rows = []
    now = datetime.now(timezone.utc)
    for day in range(1, n_days + 1):
        for j in range(n_per_day):
            ts = now - timedelta(days=day, seconds=j * 3600)
            rows.append(
                {
                    "title": f"Day{day}-{j}",
                    "url": f"https://e.com/d{day}a{j}",
                    "published_at": ts.isoformat(),
                    "first_seen": ts.isoformat(),  # indexed same-day — a healthy feed
                }
            )
    return rows


# ── clock param: published vs seen ────────────────────────────────────────────────


def test_is_spike_published_clock_default():
    rows = _established_baseline(1, _SPIKE_BASELINE_DAYS) + [
        {
            "title": f"surge-{i}",
            "url": f"https://e/{i}",
            "published_at": _iso_ago(_SPIKE_WINDOW_SEC // 2),
            "first_seen": _iso_ago(_SPIKE_WINDOW_SEC // 2),
        }
        for i in range(_SPIKE_MIN_ABS + 5)
    ]
    spike, count = _is_spike(rows, clock="published")
    assert spike is True
    assert count >= _SPIKE_MIN_ABS


def test_is_spike_seen_clock_reads_first_seen_not_published():
    """A wave with OLD published_at but RECENT first_seen fires on clock='seen' only."""
    baseline = _established_baseline(1, _SPIKE_BASELINE_DAYS)
    late_wave = [
        {
            "title": f"late-{i}",
            "url": f"https://e/late{i}",
            "published_at": _iso_days_ago(3),  # old — outside the 90-min publish window
            "first_seen": _iso_ago(60),  # just arrived
        }
        for i in range(_SPIKE_MIN_ABS + 5)
    ]
    rows = baseline + late_wave

    spike_published, _ = _is_spike(rows, clock="published")
    assert spike_published is False  # publish-clock MISSES it (born stale)

    spike_seen, count_seen = _is_spike(rows, clock="seen")
    assert spike_seen is True  # arrival-clock CATCHES it
    assert count_seen >= _SPIKE_MIN_ABS


# ── _is_spike_any_clock: OR-combination ───────────────────────────────────────────


def test_is_spike_any_clock_fires_on_published_clock_alone():
    rows = _established_baseline(1, _SPIKE_BASELINE_DAYS) + [
        {
            "title": f"surge-{i}",
            "url": f"https://e/{i}",
            "published_at": _iso_ago(_SPIKE_WINDOW_SEC // 2),
            "first_seen": _iso_ago(_SPIKE_WINDOW_SEC // 2),
        }
        for i in range(_SPIKE_MIN_ABS + 5)
    ]
    spike, _ = _is_spike_any_clock(rows)
    assert spike is True


def test_is_spike_any_clock_july14_replay_late_indexed_wave():
    """THE July-14 Uber/ONWAI replay fixture: Bloomberg breaks same-day, but syndicated
    copies index hours later — published_at predates the recent window even though the copies
    just landed. The publish-only check (pre-INT5 behavior) misses this; _is_spike_any_clock
    catches it via the arrival clock."""
    baseline = _established_baseline(1, _SPIKE_BASELINE_DAYS)
    late_indexed_wave = [
        {
            "title": f"Uber ONWAI coverage {i}",
            "url": f"https://e.com/onwai-late{i}",
            "published_at": _iso_days_ago(1) if i % 2 else _iso_ago(_SPIKE_WINDOW_SEC * 3),
            "first_seen": _iso_ago(120),  # arrived just now
        }
        for i in range(_SPIKE_MIN_ABS + 4)
    ]
    rows = baseline + late_indexed_wave

    assert _is_spike(rows, clock="published")[0] is False  # would have been silently missed
    fired, count = _is_spike_any_clock(rows)
    assert fired is True
    assert count >= _SPIKE_MIN_ABS


def test_is_spike_any_clock_cold_start_backlog_still_blocked_on_both_clocks():
    """REGRESSION GUARD: the INT3 cold-start fix must not be undone by adding the arrival
    clock. A first-ever backlog fetch (many rows, ALL first_seen=now, no history on EITHER
    clock) must not fire — on published (old INT3 guard) OR on seen (new INT5 guard, since a
    backlog dumped in one poll has near-zero first_seen SPREAD)."""
    n = max(20, _SPIKE_MIN_ABS * 3)
    backlog = [
        {
            "title": f"Old Article {i}",
            "url": f"https://e.com/backlog{i}",
            "published_at": _iso_days_ago(3 + i * 0.1),
            "first_seen": _iso_ago(30),  # all fetched in the SAME poll — no spread
        }
        for i in range(n)
    ]
    fired, _ = _is_spike_any_clock(backlog)
    assert fired is False


def test_is_spike_any_clock_below_floor_never_fires():
    rows = _established_baseline(1, _SPIKE_BASELINE_DAYS)
    fired, count = _is_spike_any_clock(rows[: _SPIKE_MIN_ABS - 1])
    assert fired is False


def test_is_spike_min_history_days_guards_both_clocks():
    # Only 1 day of history (< _SPIKE_MIN_HISTORY_DAYS) on EITHER clock → no fire regardless
    # of count.
    assert _SPIKE_MIN_HISTORY_DAYS >= 1
    rows = [
        {
            "title": f"x{i}",
            "url": f"https://e/{i}",
            "published_at": _iso_ago(60),
            "first_seen": _iso_ago(60),
        }
        for i in range(_SPIKE_MIN_ABS + 10)
    ]
    # All timestamps cluster at "now" — near-zero span on either clock.
    assert _is_spike_any_clock(rows)[0] is False


def test_spike_factor_and_min_abs_overrides_forwarded_to_both_clocks():
    rows = _established_baseline(1, _SPIKE_BASELINE_DAYS) + [
        {
            "title": f"s{i}",
            "url": f"https://e/{i}",
            "published_at": _iso_ago(60),
            "first_seen": _iso_ago(60),
        }
        for i in range(2)
    ]
    # Default floor (5) would reject 2 recent articles outright; a lowered floor of 2 lets the
    # factor check decide — baseline rate here is well under 1/window, so the factor gate
    # clamps to spike_factor * 1.0 = 2.0, and count(2) >= 2.0 fires.
    fired, count = _is_spike_any_clock(rows, spike_min_abs=2, spike_factor=2.0)
    assert count == 2
    assert fired is True
