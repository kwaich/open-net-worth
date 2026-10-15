"""
daily_total_value — daily total-value + total-return series feed (investments redesign).

Two consumers:
  1. Masthead 30D sparkline: GET /api/portfolio/sparkline — cheap slice, real-data-only gate.
  2. Risk-metrics engine (sys-risk-metrics-engine lane): consumes the daily_return vector
     via total_value_series; that lane owns risk formulae (RFC §4 resolution 1).

Core design:
  - price-only reconstructed value from portfolio_history.portfolio_value_series (daily,
    forward-sweep quantities — the shared primitive that already handles mid-window trades).
  - reinvested+cash income overlaid via IncomeEventStore + income.event_base_amount (the
    "never trust stored base_amount" invariant — recomputed from native leg + current FX).
  - tr_index = (value_base + income_cum_base) / base_level; daily_return = index_t/index_{t-1} - 1.
  - has_real_daily_history gate: FALSE → callers MUST omit the sparkline (no random walk).
  - Pure / computed-not-stored — no new store, no migration, no choke point.

Layering: api → services ← db ← core. No api/ai import.
Siblings (portfolio_history, returns, income) lazy-imported inside functions to avoid cycles.
"""

from __future__ import annotations

import logging
from datetime import date

from backend.core.fx import base_currency
from backend.core.user_time import user_today

_log = logging.getLogger(__name__)

# A window needs at least this many distinct value points for the series to be meaningful.
MIN_VALUE_POINTS = 2

# Annualization factor for volatility/Sharpe (calendar-day series, one point per day).
# RFC Open Question 5 defers the trading-day vs calendar-day choice; v1 uses 365 to match
# the grain of the carry-forward-filled series (each calendar day = one point).
_ANNUALIZATION_FACTOR = 365.0


# ── Internal helpers ────────────────────────────────────────────────────────────


def _load_rates() -> dict:
    """Current cached FX rates, or pivot-only fallback. Defensive — a missing/corrupt
    fx_cache never crashes a pure read (mirrors portfolio_history._load_rates_safe)."""
    try:
        from backend.services.fx_cache import load_fx_cache

        return load_fx_cache()
    except Exception:
        return {}


def _income_cum_series(
    dates: list[str],
    income_events: list[dict],
    rates: dict,
    base: str,
) -> list[float]:
    """Running cumulative income (all kinds, cash+DRP) at each date in `dates`.

    Each IncomeEvent with paid_at <= date is added exactly ONCE (per RFC Q1 DRP convention:
    count both cash and DRP income at paid_at; the price-only value_base carries the DRP
    shares' subsequent market move — no double-count).

    Returns a list parallel to `dates` with the cumulative base-amount sums.
    """
    from backend.services import income as income_svc

    # Events without a paid_at cannot be placed in the timeline — exclude them.
    # Including them would bucket them on day-1 (empty string < any date) and inflate the TR index.
    evs = sorted(
        [e for e in income_events if e.get("paid_at")],
        key=lambda e: e.get("paid_at") or "",
    )
    ev_idx = 0
    n_evs = len(evs)
    running = 0.0
    result: list[float] = []

    for d in dates:
        while ev_idx < n_evs and (evs[ev_idx].get("paid_at") or "") <= d:
            running += income_svc.event_base_amount(evs[ev_idx], rates=rates, base=base)
            ev_idx += 1
        result.append(round(running, 2))

    return result


def _build_tr_index(
    value_bases: list[float],
    income_cums: list[float],
) -> tuple[list[float | None], list[float | None]]:
    """Compute total-return index and daily_return from the overlaid value + income series.

    tr_index_t = (value_base_t + income_cum_t) / base_level
    base_level = first point with positive (value_base + income_cum)

    daily_return_t = tr_index_t / tr_index_{t-1} - 1  (None on first point or prev_index <= 0)
    """
    n = len(value_bases)
    tr_index: list[float | None] = [None] * n
    daily_return: list[float | None] = [None] * n

    base_level: float | None = None
    prev_index: float | None = None

    for i in range(n):
        total = value_bases[i] + income_cums[i]
        if base_level is None and total > 0:
            base_level = total

        if base_level is None or base_level <= 0:
            prev_index = None
            continue

        idx = round(total / base_level, 8)
        tr_index[i] = idx

        if prev_index is not None and prev_index > 0:
            daily_return[i] = round(idx / prev_index - 1.0, 8)

        prev_index = idx

    return tr_index, daily_return


# ── Public service functions ────────────────────────────────────────────────────


def total_value_series(
    start: str,
    end: str,
    *,
    today: date | None = None,
    income_events: list[dict] | None = None,
    primitive_result: dict | None = None,
    rates: dict | None = None,
) -> dict:
    """Daily total-VALUE + total-RETURN series in base currency over [start, end].

    Overlays reinvested+cash income (IncomeEventStore, recomputed base) onto the
    price-only reconstructed value series from portfolio_history.portfolio_value_series.
    Pure / computed-not-stored. Returns the shape documented in the RFC §schema.

    Injectable parameters (for tests): income_events, primitive_result, rates.
    """
    from backend.db.income_event_store import IncomeEventStore
    from backend.services import portfolio_history as ph

    base = base_currency()
    if rates is None:
        rates = _load_rates()

    if primitive_result is None:
        primitive_result = ph.portfolio_value_series(start, end, "daily", rates=rates)

    raw_series = primitive_result.get("series") or []
    data_quality_in = primitive_result.get("data_quality") or {}

    if income_events is None:
        income_events = IncomeEventStore().query_events(
            date_from=start,
            date_to=end,
        )

    dates = [pt["date"] for pt in raw_series]
    value_bases = [float(pt.get("value_base") or 0) for pt in raw_series]
    classes_list = [pt.get("classes") or {} for pt in raw_series]

    income_cums = _income_cum_series(dates, income_events, rates, base) if dates else []

    tr_indices, daily_returns = _build_tr_index(value_bases, income_cums) if dates else ([], [])

    series = [
        {
            "date": dates[i],
            "value_base": value_bases[i],
            "income_cum_base": income_cums[i],
            "tr_index": tr_indices[i],
            "daily_return": daily_returns[i],
            "classes": classes_list[i],
        }
        for i in range(len(dates))
    ]

    n_points = len(series)
    market_since = data_quality_in.get("market_since")
    has_real = n_points >= MIN_VALUE_POINTS and bool(market_since)

    data_quality = {
        "market_since": market_since,
        "inception": data_quality_in.get("inception"),
        "value_points": n_points,
        "sufficient": has_real,
        "has_real_daily_history": has_real,
        "fx_approximate": data_quality_in.get("fx_approximate", False),
        "has_unrecorded_risk": data_quality_in.get("has_unrecorded_risk", False),
        "tiers": data_quality_in.get("tiers") or {},
    }

    return {
        "base_currency": base,
        "grain": "daily",
        "start": dates[0] if dates else start,
        "end": dates[-1] if dates else end,
        "series": series,
        "data_quality": data_quality,
    }


def sparkline_series(days: int = 30, *, today: date | None = None) -> dict:
    """Thin masthead helper: the last `days` of {date, value_base, income_cum_base}.

    Sets data_quality.has_real_daily_history; the caller omits the chart when False.
    Never synthesizes data — empty/false is the honest posture when history is thin.
    """
    from backend.services import returns as returns_svc

    t = today or user_today()
    end = t.isoformat()
    start_dt = t.__class__.fromordinal(t.toordinal() - days)
    start = start_dt.isoformat()

    # Reuse resolve_period's price floor logic for the effective start.
    price_floor = None
    try:
        from backend.db.price_history_store import PriceHistoryStore

        price_floor = PriceHistoryStore().earliest_date()
    except Exception:
        pass

    effective_start = returns_svc._effective_window(start, end, None, price_floor)

    result = total_value_series(effective_start, end, today=t)
    series = result.get("series") or []

    return {
        "base_currency": result.get("base_currency"),
        "days": days,
        "series": [
            {
                "date": pt["date"],
                "value_base": pt["value_base"],
                "income_cum_base": pt["income_cum_base"],
            }
            for pt in series
        ],
        "data_quality": result.get("data_quality") or {},
    }
