"""Shared news-spike detection primitives — PUBLISH-BASED, BASELINE-RELATIVE.

Split out of rss_poller.py (INT3) so the fast-tier (held assets, subject ``asset:{iid}``)
and slow-tier (watchlist topics, subject ``topic:{item_id}``) polling paths share IDENTICAL
math instead of two copies drifting apart. Every function here takes plain evidence rows —
it never reads a subject string or knows what kind of thing is being watched — so a topic and
an asset spike the same way; the only difference between callers is which cache subject they
read before calling in.

A spike fires when the recent-window published count is BOTH:
  (a) >= spike_min_abs (absolute floor, prevents noise on tiny feeds), AND
  (b) >= spike_factor × the subject's own trailing baseline (mean articles per same-size
      window over the trailing _SPIKE_BASELINE_DAYS days from cached evidence).
Cold-start guard: if the evidence cache covers < _SPIKE_MIN_HISTORY_DAYS days of data, no
spike fires (insufficient baseline).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

_SPIKE_WINDOW_SEC = 5400  # recent-window: 90 min (matches fast-poll cadence × headroom;
# the slow tier runs every ~30 min, so 90 min still comfortably spans 2-3 slow ticks — a spike
# that started between polls is never missed, and the baseline math below is window-size-agnostic
# so reusing this exact constant for the slow/topic path needs no separate tuning)
_SPIKE_MIN_ABS = 5  # absolute floor: need at least 5 published-recently articles
_SPIKE_FACTOR = 3.0  # must be >= 3× the trailing baseline rate
_SPIKE_BASELINE_DAYS = 7  # trailing period to build per-subject baseline
_SPIKE_MIN_HISTORY_DAYS = 2  # cold-start guard: don't fire if < 2 days of cached data
_SPIKE_COOLDOWN_HOURS = 6
_SPIKE_MAX_PER_POLL = 3  # cap on news_spike enqueues per TIER per poll tick — every tier
# (fast holdings, fast topics, slow holdings+topics) tracks its OWN counter (INT7), so a busy
# news day for one never crowds out another. Lives here (not rss_poller.py) so both
# rss_poller.py's holding processing and rss_poller_topics.py's topic processing share ONE cap.


def _parse_published_at(value: str) -> datetime | None:
    """Parse an RSS/Atom published timestamp tolerantly.

    Accepts:
      - ISO 8601 (with or without timezone, with or without microseconds)
      - RFC 2822 (common in RSS: 'Mon, 01 Jan 2024 12:00:00 +0000')

    Returns a timezone-aware datetime, or None if unparseable / missing.
    """
    s = (value or "").strip()
    if not s:
        return None
    # Try ISO 8601 first (most common in modern feeds)
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        pass
    # Fallback: RFC 2822 (classic RSS <pubDate>)
    try:
        return parsedate_to_datetime(s)
    except Exception:
        return None


def _clock_field(clock: str) -> str:
    """Which evidence-row timestamp a clock mode reads. 'published' (default, INT3) uses the
    article's own publish stamp; 'seen' (INT5, news-ingestion-latency.md §4) uses first_seen —
    WHEN WE noticed it — so a story that published hours ago but only just landed in our cache
    (a late-indexed wire copy) can still register as a surge."""
    return "first_seen" if clock == "seen" else "published_at"


def _count_published_in_window(rows: list[dict], since: datetime, clock: str = "published") -> int:
    """Count evidence rows whose `clock` timestamp falls at or after `since`."""
    field = _clock_field(clock)
    count = 0
    for r in rows:
        dt = _parse_published_at(r.get(field) or "")
        if dt is not None and dt >= since:
            count += 1
    return count


def _spike_baseline(
    rows: list[dict], window_sec: int, clock: str = "published"
) -> tuple[float, float]:
    """Compute the trailing baseline rate and data span for a subject's evidence rows, reading
    whichever timestamp `clock` selects.

    Uses the trailing _SPIKE_BASELINE_DAYS period to estimate the mean number of
    articles published per window_sec interval. Also returns the span (days) of
    evidence in the cache so the caller can apply the cold-start guard — a first-ever backlog
    fetch has near-zero SPREAD on either clock (every row lands in one poll), so this guard
    blocks a cold start on 'seen' exactly as it always has on 'published'.

    Returns (baseline_count_per_window, history_span_days).
    """
    field = _clock_field(clock)
    now = datetime.now(timezone.utc)
    baseline_start = now - timedelta(days=_SPIKE_BASELINE_DAYS)

    times: list[datetime] = []
    for r in rows:
        dt = _parse_published_at(r.get(field) or "")
        if dt is not None and dt >= baseline_start:
            times.append(dt)

    if not times:
        return 0.0, 0.0

    earliest = min(times)
    latest = max(times)
    span_days = (latest - earliest).total_seconds() / 86400.0

    # Number of non-overlapping windows in the baseline period
    baseline_period_sec = _SPIKE_BASELINE_DAYS * 86400.0
    n_windows = baseline_period_sec / window_sec
    baseline_rate = len(times) / n_windows

    return baseline_rate, span_days


def _is_spike(
    rows: list[dict],
    spike_min_abs: int = _SPIKE_MIN_ABS,
    spike_factor: float = _SPIKE_FACTOR,
    clock: str = "published",
) -> tuple[bool, int]:
    """Determine whether the recent-window count (on `clock`'s timestamp) constitutes a spike.

    Returns (spike_detected, recent_count).
    ``spike_min_abs`` and ``spike_factor`` override the module-level constants when provided
    (read from config in run_rss_poll so per-user tuning is respected without changing defaults).
    ``clock`` selects 'published' (default, INT3) or 'seen' (INT5 arrival-clock — see
    _is_spike_any_clock, which OR-combines both)."""
    now = datetime.now(timezone.utc)
    since = now - timedelta(seconds=_SPIKE_WINDOW_SEC)

    recent_count = _count_published_in_window(rows, since, clock)
    if recent_count < spike_min_abs:
        return False, recent_count

    baseline_rate, history_span_days = _spike_baseline(rows, _SPIKE_WINDOW_SEC, clock)

    if history_span_days < _SPIKE_MIN_HISTORY_DAYS:
        return False, recent_count

    if recent_count >= spike_factor * max(baseline_rate, 1.0):
        return True, recent_count

    return False, recent_count


def _is_spike_any_clock(
    rows: list[dict],
    spike_min_abs: int = _SPIKE_MIN_ABS,
    spike_factor: float = _SPIKE_FACTOR,
) -> tuple[bool, int]:
    """OR-combine the publish-clock (INT3) and arrival-clock (INT5) spike checks.

    A genuine surge fires on EITHER clock: publish-clock catches an on-time-indexed surge;
    arrival-clock additionally catches a LATE-INDEXED wave whose published_at predates the
    recent window even though it just landed in our cache — the July-14 Uber/Kestrel Dash
    incident's exact failure mode ("born stale": a story arriving hours after its publish stamp
    can never trip a publish-only counter). This "replaces nothing — runs alongside" the
    existing publish-clock check (news-ingestion-latency.md §4) rather than switching wholesale,
    because the arrival-clock's OWN cold-start guard (near-zero first_seen SPREAD on a first-
    ever backlog fetch) already protects it exactly the way the publish-clock guard always has —
    so adding it is strictly additive, never reopening the INT3 cold-start regression."""
    spike, count = _is_spike(rows, spike_min_abs, spike_factor, clock="published")
    if spike:
        return True, count
    return _is_spike(rows, spike_min_abs, spike_factor, clock="seen")
