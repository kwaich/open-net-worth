"""
forecast_scorecard_service — did our projections match reality? (P3-SI1)

The self-improvement proof. The app makes two forward claims about money:
  * a CASH-FLOW forecast — the recurring monthly baseline (rolling-average income minus
    expenses) that drives ``GET /api/cashflow``'s forward months, and
  * a NET-WORTH trajectory — that baseline anchored on a starting net worth and walked
    forward month by month.

This module REPLAYS those same claims over a TRAILING window (1M/3M/6M/1Y) and scores them
against what actually happened, returning per-period deltas + an accuracy summary. It is the
honest mirror the rest of the app never had: "we said you'd net +X; you netted +Y".

Compute-only, base-currency-normalized, NOTHING stored (the CLAUDE.md invariant). Every
input is REUSED, never re-walked or snapshotted:
  * ACTUAL net worth   — ``ledger.net_worth_history()`` (already FX-normalized, month-end
                         points). The actual net-worth path.
  * ACTUAL cash flow   — real income/expense transactions per month, each recomputed into
                         the current base via ``ledger.txn_base_amount`` (never the stale
                         stored ``base_amount`` — same rule as the cash-flow summaries).
  * THE FORECAST       — the recurring baseline = ``cashflow._rolling_avg_income`` minus
                         ``cashflow._rolling_avg_expenses`` (the SAME numbers the live
                         projection uses), held flat across the window. This is the single
                         forecast assumption, so the scorecard scores the projection the user
                         actually sees, not a parallel model.

Imports are downward/sideways only (services -> db -> core; services<->services allowed):
reuses ``ledger`` + ``cashflow`` (sibling services) and reads no store directly beyond what
they expose. No api/ai import (Contract 3). Pure math; nothing is written.

Design: P3-SI1 (forecast-vs-actuals scorecard).
"""

from __future__ import annotations

import logging
from datetime import date

from backend.core.user_time import user_today
from backend.db import read_cache
from backend.services import cashflow as cashflow_service
from backend.services import ledger as ledger_service
from backend.services.budgets import is_spending_expense
from backend.services.fx_cache import load_fx_cache

_log = logging.getLogger(__name__)

# The trailing windows the API enum / UI toggle expose, mapped to a month count. A window is
# scored over the WHOLE past months it spans (the current, still-incomplete month is excluded
# — you can't score a forecast against an unfinished month).
WINDOWS: dict[str, int] = {"1M": 1, "3M": 3, "6M": 6, "1Y": 12}
DEFAULT_WINDOW = "3M"

# A window needs at least this many fully-elapsed actual months with a real net-worth point to
# produce a non-empty scorecard. Below it we return zeros (an empty/young instance), not a
# fabricated accuracy figure.
MIN_PERIODS = 1


# ── Month helpers (reuse cashflow's arithmetic — one source of truth) ──────────────


def _months_back(window: str) -> int:
    """The number of fully-elapsed past months a window spans (>= 1; unknown → default)."""
    return WINDOWS.get((window or "").upper(), WINDOWS[DEFAULT_WINDOW])


def _ym(year: int, month: int) -> str:
    return f"{year:04d}-{month:02d}"


def _net_worth_by_month(history: list[dict]) -> dict[str, float]:
    """Map ``YYYY-MM`` → actual net worth at that month's recorded point.

    ``net_worth_history`` emits one point per calendar month (month-end, except the current
    month which is today). Keying by ``YYYY-MM`` lets the scorecard look up the actual net
    worth at any window boundary without re-walking the ledger.
    """
    out: dict[str, float] = {}
    for pt in history:
        d = (pt.get("date") or "")[:7]
        if d:
            out[d] = float(pt.get("net_worth") or 0.0)
    return out


def _actual_month_flow(
    year: int, month: int, base: str, rates: dict, all_txns: list[dict] | None = None
) -> tuple[float, float]:
    """Real (income_in, expense_out) for one calendar month, in the current base currency.

    Folds the actual income/expense transactions of the month, each recomputed from its native
    leg via ``ledger.txn_base_amount`` (never the stored snapshot — survives a base change),
    mirroring ``cashflow._historical_months``. Transfers/adjustments are not flow, and neither is
    the investment leg (``is_spending_expense`` drops it) — otherwise a lumpy security buy reads as
    a spending spike and corrupts the cash-flow miss / bias the scorecard reports.

    ``all_txns`` (pre-loaded, already deleted-filtered) lets ``scorecard()`` load the ledger
    ONCE and reuse it across every scored month instead of re-reading the whole store per
    month (measured: a 1Y window did 24 separate full-file reads here alone). Falls back to
    the per-call store query when not supplied, so a standalone/test call still works."""
    date_from, date_to = cashflow_service._month_range(year, month)
    svc = ledger_service
    if all_txns is not None:
        inc = cashflow_service._filter_txns(
            all_txns, kind="income", date_from=date_from, date_to=date_to
        )
        exp_candidates = cashflow_service._filter_txns(
            all_txns, kind="expense", date_from=date_from, date_to=date_to
        )
    else:
        inc = svc.transactions.query_txns(kind="income", date_from=date_from, date_to=date_to)
        exp_candidates = svc.transactions.query_txns(
            kind="expense", date_from=date_from, date_to=date_to
        )
    exp = [t for t in exp_candidates if is_spending_expense(t)]
    total_in = round(sum(svc.txn_base_amount(t, base, rates) for t in inc), 2)
    total_out = round(sum(svc.txn_base_amount(t, base, rates) for t in exp), 2)
    return total_in, total_out


# ── Accuracy summary ────────────────────────────────────────────────────────────


def _accuracy(periods: list[dict], key: str) -> dict:
    """Accuracy stats for one lens over the scored ``periods``.

    ``key`` selects which delta to score (``cashflow`` → net cash-flow delta; ``net_worth`` →
    net-worth delta). Returns:
      * ``mean_abs_error``  — mean absolute base-currency miss per month (how far off, $).
      * ``mean_abs_pct_error`` (MAPE) — mean |actual-projected|/|actual| over months with a
        non-trivial actual (the headline "how accurate were we?" %); null when none qualify.
      * ``bias``            — mean SIGNED delta (actual-projected): positive = we UNDER-
        forecast (reality beat the plan), negative = we OVER-forecast. The direction of the
        miss, not just its size.
      * ``periods_scored``  — how many months fed the stats.
    All compute-only; an empty list yields zeros/null (never a divide-by-zero).
    """
    if not periods:
        return {
            "mean_abs_error": 0.0,
            "mean_abs_pct_error": None,
            "bias": 0.0,
            "periods_scored": 0,
        }
    deltas = [p[f"{key}_delta"] for p in periods]
    abs_errors = [abs(d) for d in deltas]
    mean_abs = round(sum(abs_errors) / len(abs_errors), 2)
    bias = round(sum(deltas) / len(deltas), 2)
    # Per-month term CAPPED at 100%. Cash-flow NET is often near break-even, so a small actual
    # against a normal miss made plain MAPE explode into absurd headline values (e.g. 9777%);
    # capping each month at "100% off" bounds the mean without distorting genuinely-close months.
    pct_terms = [
        min(abs(d) / abs(p[f"{key}_actual"]), 1.0)
        for p, d in zip(periods, deltas)
        if abs(p[f"{key}_actual"]) > 1.0
    ]
    mape = round(100.0 * sum(pct_terms) / len(pct_terms), 1) if pct_terms else None
    return {
        "mean_abs_error": mean_abs,
        "mean_abs_pct_error": mape,
        "bias": bias,
        "periods_scored": len(periods),
    }


# ── Public entrypoint ──────────────────────────────────────────────────────────────


@read_cache.cached(
    lambda window=DEFAULT_WINDOW, today=None: (window, today.isoformat() if today else None)
)
def scorecard(window: str = DEFAULT_WINDOW, today: date | None = None) -> dict:
    """Forecast-vs-actuals scorecard over the trailing ``window`` (1M/3M/6M/1Y).

    Replays the live recurring cash-flow baseline (rolling-average income minus expenses) and
    the net-worth trajectory it implies across the past months of the window, scoring each
    against the actual ledger:

      * Per month — projected vs actual NET CASH FLOW and projected vs actual NET WORTH, plus
        the signed delta for each (actual minus projected).
      * Two ``accuracy`` blocks (cashflow, net_worth) — mean absolute error, MAPE, and signed
        bias (see ``_accuracy``).

    The forecast is the SAME baseline the user sees in ``/api/cashflow`` (reused, not a parallel
    model). The net-worth projection anchors on the actual net worth at the window's OPENING
    month-end, then walks forward adding the projected monthly net — so it scores drift from the
    plan, not the absolute level. ``forecastable`` is False (with zeroed/empty figures, never a
    500) when there's no recurring baseline yet (a brand-new instance with no income/expense
    history) or fewer than ``MIN_PERIODS`` elapsed actual months.
    """
    today = today or user_today()
    base = ledger_service.base_currency()
    months_back = _months_back(window)

    # ONE ledger read feeds the whole pipeline. Profiled 2026-08-12: this function's legs
    # (projection, both rolling-average probes, net-worth history, its own month-flow scan)
    # triggered transaction_store.all() FIVE times per request — 168ms of the 463ms total.
    all_txns = [t for t in ledger_service.transactions.all() if not t.get("deleted")]

    # The recurring baseline = the live projection's monthly assumption (reused, not re-derived).
    # Since the stream model shipped (.claude/rfcs/2026-07-27-cashflow-stream-model.md) that
    # assumption is "streams + variable residual", NOT the flat rolling average — so this reads
    # the projection's own reported headline instead of recomputing the averages it retired.
    # Scoring the retired formula would grade a forecast the user is no longer shown.
    projection = cashflow_service.build_projection(months=1, history_months=0, all_txns=all_txns)
    # VALUES come from the projection; PRESENCE still comes from the rolling-average probe,
    # which returns None only when a side has no transactions at all. The projection reports
    # floats unconditionally, so reading presence off it would turn "no expense history yet"
    # into a confident "your expenses are 0.00" — a different, wrong claim.
    proj_income = (
        projection.get("monthly_income_avg")
        if cashflow_service._rolling_avg_income(base, all_txns) is not None
        else None
    )
    proj_expenses = (
        projection.get("monthly_expenses_avg")
        if cashflow_service._rolling_avg_expenses(base, all_txns) is not None
        else None
    )
    has_baseline = proj_income is not None or proj_expenses is not None
    projected_net = round((proj_income or 0.0) - (proj_expenses or 0.0), 2)

    history = ledger_service.net_worth_history(all_txns=all_txns)
    nw_by_month = _net_worth_by_month(history)
    rates = load_fx_cache()

    # Window opens at the month-end BEFORE the first scored month (the anchor for the net-worth
    # projection). Scored months are the `months_back` fully-elapsed months ending last month.
    anchor_y, anchor_m = cashflow_service._sub_months(today.year, today.month, months_back + 1)
    anchor_ym = _ym(anchor_y, anchor_m)
    anchor_nw = nw_by_month.get(anchor_ym)

    periods: list[dict] = []
    cumulative_proj = anchor_nw if anchor_nw is not None else 0.0
    for k in range(months_back, 0, -1):  # oldest → newest of the elapsed window
        y, m = cashflow_service._sub_months(today.year, today.month, k)
        ym = _ym(y, m)
        actual_nw = nw_by_month.get(ym)
        if actual_nw is None:
            continue  # no recorded net-worth point this month → not scoreable
        total_in, total_out = _actual_month_flow(y, m, base, rates, all_txns)
        actual_net = round(total_in - total_out, 2)
        cumulative_proj = round(cumulative_proj + projected_net, 2)
        projected_nw = cumulative_proj if anchor_nw is not None else None
        period = {
            "year_month": ym,
            "label": date(y, m, 1).strftime("%b %Y"),
            "cashflow_projected": projected_net,
            "cashflow_actual": actual_net,
            "cashflow_delta": round(actual_net - projected_net, 2),
            "net_worth_actual": round(actual_nw, 2),
            "net_worth_projected": round(projected_nw, 2) if projected_nw is not None else None,
            "net_worth_delta": (
                round(actual_nw - projected_nw, 2) if projected_nw is not None else None
            ),
        }
        periods.append(period)

    forecastable = has_baseline and len(periods) >= MIN_PERIODS
    # Net-worth periods are only scoreable when we had an opening anchor to project from.
    nw_periods = [p for p in periods if p["net_worth_delta"] is not None]

    return {
        "window": (window or DEFAULT_WINDOW).upper(),
        "base_currency": base,
        "forecastable": forecastable,
        "projected_monthly_net": projected_net,
        "projected_monthly_income": round(proj_income, 2) if proj_income is not None else None,
        "projected_monthly_expenses": (
            round(proj_expenses, 2) if proj_expenses is not None else None
        ),
        "periods": periods if forecastable else [],
        "cashflow_accuracy": _accuracy(periods if forecastable else [], "cashflow"),
        "net_worth_accuracy": _accuracy(nw_periods if forecastable else [], "net_worth"),
        "data_quality": {
            "has_baseline": has_baseline,
            "periods_available": len(periods),
            "net_worth_anchored": anchor_nw is not None,
        },
    }
