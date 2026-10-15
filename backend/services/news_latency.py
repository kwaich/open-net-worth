"""Per-source publish→arrival latency observability (news-ingestion-latency.md §5) — a
read-only aggregation over EvidenceStore rows, keyed by each row's ``via`` (the channel
provenance every feeds.py/feeds_hot.py/feeds_regulatory.py resolver stamps: rss:yahoo,
rss:google_news, rss:fn_adhoc, atom:edgar, api:gdelt, rss:custom, ...).

This is how source rot becomes VISIBLE — nothing here writes anything; it just measures how
long each source took, on average, between an article's own publish stamp and the moment WE
first saw it (``first_seen``). Layer: services → db/core only (no api/monitoring/ai imports).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

# Reuses evidence.py's exact tolerant timestamp parser (ISO-8601 + RFC-2822 + implausible-
# future guard) rather than duplicating it — both modules read the SAME evidence-row fields.
from backend.services.evidence import _parse_published


def _lag_hours(row: dict) -> float | None:
    """Hours between an evidence row's publish stamp and when WE first saw it. None when
    either timestamp is missing/unparseable — an unknown lag is unknown, never coerced to 0."""
    published = _parse_published(row.get("published_at"))
    first_seen = _parse_published(row.get("first_seen"))
    if published is None or first_seen is None:
        return None
    return (first_seen - published).total_seconds() / 3600.0


def _percentile(sorted_vals: list[float], pct: float) -> float:
    if not sorted_vals:
        return 0.0
    idx = min(len(sorted_vals) - 1, round(pct * (len(sorted_vals) - 1)))
    return sorted_vals[idx]


def lag_stats(days: int = 7, store=None) -> dict[str, dict]:
    """Per-`via` ``{n, median_hours, p75_hours}`` over evidence first-seen within the trailing
    `days` (default 7). A row with an unparseable timestamp, or a negative lag (clock-skew
    noise — published-after-we-saw-it), is excluded from that source's stats rather than
    distorting it. Sources with zero qualifying rows are simply absent from the result."""
    from backend.db.evidence_store import EvidenceStore

    store = store or EvidenceStore()
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    by_source: dict[str, list[float]] = {}
    for row in store.all():
        first_seen = _parse_published(row.get("first_seen"))
        if first_seen is None or first_seen < cutoff:
            continue
        lag = _lag_hours(row)
        if lag is None or lag < 0:
            continue
        via = row.get("via") or "unknown"
        by_source.setdefault(via, []).append(lag)

    out: dict[str, dict] = {}
    for via, lags in by_source.items():
        lags.sort()
        out[via] = {
            "n": len(lags),
            "median_hours": round(_percentile(lags, 0.5), 2),
            "p75_hours": round(_percentile(lags, 0.75), 2),
        }
    return out


def last_ok_at(store=None) -> dict[str, str]:
    """Per-`via` MAX(first_seen) across ALL evidence — UNBOUNDED, unlike `lag_stats`' trailing-N-
    days window. Distinguishes "this source hasn't produced a hit in over 7 days" (still present
    here, absent from `lag_stats`) from "this source has never produced a hit at all" (absent
    from both) — INT9 Phase B's `last_ok_at` health-join field. ISO-8601 string per via; a via
    with no parseable `first_seen` anywhere is simply absent from the result."""
    from backend.db.evidence_store import EvidenceStore

    store = store or EvidenceStore()
    best: dict[str, datetime] = {}
    for row in store.all():
        seen = _parse_published(row.get("first_seen"))
        if seen is None:
            continue
        via = row.get("via") or "unknown"
        if via not in best or seen > best[via]:
            best[via] = seen
    return {via: ts.isoformat() for via, ts in best.items()}
