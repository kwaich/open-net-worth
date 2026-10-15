"""
GET /api/portfolio/value-series

Returns the investment-book's total value (base currency) at each daily or monthly
grain date over the trailing `days` window.  All math lives in
`services/portfolio_history.portfolio_value_series`; this router only validates
the query params and shapes the response.

Response: {
    series: [{date: ISO, value_base: number}],
    inception: ISO | null,
    has_real_daily_history: bool
}
`has_real_daily_history` is false when the portfolio has fewer than 2 priced days —
the frontend must omit the sparkline entirely when this flag is false or series < 2.
`holdings_series` emits one point per calendar day regardless of whether any price
exists, so the gate cannot rely on `len(series)` alone: it also requires
`data_quality.market_since` to be set and at least 2 series points to fall on or
after it — otherwise an empty/young book's all-zero series would read as real.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Literal

from fastapi import APIRouter, HTTPException, Query

from backend.core.user_time import user_today
from backend.services import portfolio_history as ph

router = APIRouter()


@router.get("/api/portfolio/value-series")
def get_portfolio_value_series(
    days: int = Query(30, ge=1, le=365),
    grain: Literal["daily", "monthly"] = Query("daily"),
):
    end = user_today()
    start = end - timedelta(days=days)
    try:
        result = ph.portfolio_value_series(start.isoformat(), end.isoformat(), grain)
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to compute value series")

    raw_series = result.get("series", [])
    # Strip the `classes` sub-dict — the frontend only needs the scalar total.
    series = [{"date": pt["date"], "value_base": pt["value_base"]} for pt in raw_series]

    data_quality = result.get("data_quality", {})
    inception: str | None = data_quality.get("inception")
    market_since: str | None = data_quality.get("market_since")
    priced_points = [pt for pt in series if market_since and pt["date"] >= market_since]
    has_real = bool(market_since) and len(priced_points) >= 2

    return {
        "series": series,
        "inception": inception,
        "has_real_daily_history": has_real,
    }
