"""
_grain — grain-date anchor generation for portfolio time series.

Generates the list of ISO date strings that define the time axis for
daily / monthly portfolio value series.  Extracted from portfolio_history
to keep that module under the line-count cap.
"""

from __future__ import annotations

import logging
from calendar import monthrange
from datetime import date

from backend.core.user_time import user_today

_log = logging.getLogger(__name__)


def _grain_dates(start: str, end: str, grain: str) -> list[str]:
    """Generate the list of ISO date strings in [start, end] for the given grain.

    "daily"   → every calendar day.
    "monthly" → last calendar day of each month + today (matching net_worth_history grain).
    """
    try:
        d_start = date.fromisoformat(start[:10])
        d_end = date.fromisoformat(end[:10])
    except (ValueError, TypeError):
        return []
    if d_start > d_end:
        return []

    today = user_today()
    out: list[str] = []

    if grain == "daily":
        from datetime import timedelta

        cur = d_start
        while cur <= d_end:
            out.append(cur.isoformat())
            cur = cur + timedelta(days=1)
    elif grain == "monthly":
        # Month-end days + today (same pattern as net_worth_history).
        y, m = d_start.year, d_start.month
        while date(y, m, 1) <= d_end:
            if y == today.year and m == today.month:
                pt = min(today, d_end)
            else:
                pt = date(y, m, monthrange(y, m)[1])
            if d_start <= pt <= d_end:
                out.append(pt.isoformat())
            m += 1
            if m > 12:
                y, m = y + 1, 1
    else:
        _log.warning("portfolio_history: unknown grain %r, falling back to daily", grain)
        return _grain_dates(start, end, "daily")

    # Deduplicate while preserving order.
    seen: set[str] = set()
    deduped: list[str] = []
    for d_iso in out:
        if d_iso not in seen:
            seen.add(d_iso)
            deduped.append(d_iso)
    return deduped
