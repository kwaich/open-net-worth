"""
Unit tests for services.forecast_scorecard — the forecast-vs-actuals math core (P3-SI1).

Hand-checked vectors pin the arithmetic (mirroring tests/test_returns_service.py): the pure
accuracy helper is exercised directly, and `scorecard()` is driven end-to-end with the
recurring baseline + net_worth_history + per-month flow monkeypatched (no live data / no
network → no integration mark).

Run:
    .venv/bin/python3 -m pytest tests/test_forecast_scorecard_service.py -q
"""

from __future__ import annotations

from datetime import date

import pytest

from backend.services import forecast_scorecard as fs

# ── _months_back ─────────────────────────────────────────────────────────────────


def test_months_back_known_windows():
    assert fs._months_back("1M") == 1
    assert fs._months_back("3M") == 3
    assert fs._months_back("6M") == 6
    assert fs._months_back("1Y") == 12


def test_months_back_unknown_defaults():
    assert fs._months_back("bogus") == fs._months_back(fs.DEFAULT_WINDOW)
    assert fs._months_back("") == fs._months_back(fs.DEFAULT_WINDOW)


# ── _accuracy (hand-checked) ───────────────────────────────────────────────────────


def test_accuracy_empty_is_zeroed_never_divides():
    a = fs._accuracy([], "cashflow")
    assert a == {
        "mean_abs_error": 0.0,
        "mean_abs_pct_error": None,
        "bias": 0.0,
        "periods_scored": 0,
    }


def test_accuracy_signed_bias_and_mape():
    # Two months. Projected net 1000 each.
    #   m1: actual 1200 → delta +200 (under-forecast), |pct| = 200/1200
    #   m2: actual  800 → delta -200 (over-forecast),  |pct| = 200/800
    periods = [
        {"cashflow_projected": 1000.0, "cashflow_actual": 1200.0, "cashflow_delta": 200.0},
        {"cashflow_projected": 1000.0, "cashflow_actual": 800.0, "cashflow_delta": -200.0},
    ]
    a = fs._accuracy(periods, "cashflow")
    assert a["mean_abs_error"] == 200.0  # (200+200)/2
    assert a["bias"] == 0.0  # (+200-200)/2 — misses cancel
    assert a["periods_scored"] == 2
    # MAPE = 100 * mean(200/1200, 200/800) = 100 * (0.16667+0.25)/2 ≈ 20.8
    assert a["mean_abs_pct_error"] == pytest.approx(20.8, abs=0.1)


def test_accuracy_skips_trivial_actuals_in_mape():
    # An actual of 0 (|actual| <= 1) is excluded from MAPE (no divide-by-zero), but still
    # counts toward mean_abs_error / bias.
    periods = [
        {"cashflow_projected": 100.0, "cashflow_actual": 0.0, "cashflow_delta": -100.0},
    ]
    a = fs._accuracy(periods, "cashflow")
    assert a["mean_abs_pct_error"] is None
    assert a["mean_abs_error"] == 100.0
    assert a["bias"] == -100.0


def test_accuracy_mape_is_capped_at_100pct():
    # A near-break-even actual against a large miss must NOT explode the headline MAPE
    # (regression: cash-flow net near zero previously produced ~9777%). Each month is capped
    # at 100% off, so a $50 actual with a $4888 miss contributes 100%, not 9776%.
    periods = [
        {"cashflow_projected": 4938.0, "cashflow_actual": 50.0, "cashflow_delta": -4888.0},
        {"cashflow_projected": 1000.0, "cashflow_actual": 1050.0, "cashflow_delta": 50.0},
    ]
    a = fs._accuracy(periods, "cashflow")
    # month 1 capped at 100%; month 2 ≈ 4.76% → mean ≈ 52.4%, never in the thousands.
    assert a["mean_abs_pct_error"] is not None
    assert 0.0 <= a["mean_abs_pct_error"] <= 100.0


# ── scorecard end-to-end ────────────────────────────────────────────────────────


def _drive(monkeypatch, *, income, expenses, nw_by_month, flow_by_month):
    """Wire scorecard()'s reused inputs to controlled fixtures (no store / FX / network)."""
    from backend.services import cashflow as cashflow_service
    from backend.services import ledger as ledger_service

    monkeypatch.setattr(ledger_service, "base_currency", lambda: "USD")
    monkeypatch.setattr(fs, "load_fx_cache", lambda: {"USD": 1.0})
    # The scorecard scores the baseline the USER IS SHOWN. Since the stream model shipped
    # (.claude/rfcs/2026-07-27-cashflow-stream-model.md) that is the projection's own
    # `monthly_*_avg` — "streams + variable residual" — not the retired flat rolling
    # averages, so stub the projection rather than the functions it no longer calls.
    monkeypatch.setattr(
        cashflow_service,
        "build_projection",
        lambda **kw: {
            "monthly_income_avg": income or 0.0,
            "monthly_expenses_avg": expenses or 0.0,
        },
    )
    # Presence probe: None still means "no transactions on this side at all", which the
    # scorecard reports as a null figure rather than a confident 0.00.
    monkeypatch.setattr(cashflow_service, "_rolling_avg_income", lambda base, *_a, **_k: income)
    monkeypatch.setattr(cashflow_service, "_rolling_avg_expenses", lambda base, *_a, **_k: expenses)
    monkeypatch.setattr(
        ledger_service,
        "net_worth_history",
        lambda *a, **k: [{"date": f"{ym}-28", "net_worth": v} for ym, v in nw_by_month.items()],
    )

    def fake_flow(y, m, base, rates, all_txns=None):
        return flow_by_month.get(fs._ym(y, m), (0.0, 0.0))

    monkeypatch.setattr(fs, "_actual_month_flow", fake_flow)


def test_scorecard_empty_instance_not_forecastable(monkeypatch):
    # No baseline (no income/expense history) → forecastable False, zeroed, never 500.
    _drive(monkeypatch, income=None, expenses=None, nw_by_month={}, flow_by_month={})
    out = fs.scorecard("3M", today=date(2026, 6, 6))
    assert out["forecastable"] is False
    assert out["periods"] == []
    assert out["cashflow_accuracy"]["periods_scored"] == 0
    assert out["net_worth_accuracy"]["mean_abs_pct_error"] is None
    assert out["data_quality"]["has_baseline"] is False


def test_scorecard_scores_each_elapsed_month(monkeypatch):
    # today = 2026-06-06; 3M window scores Mar/Apr/May 2026 (the elapsed months); the current
    # month (Jun) is excluded. Anchor = Feb 2026 net worth (the opening month-end).
    # Projected monthly net = income(5000) - expenses(3000) = +2000.
    _drive(
        monkeypatch,
        income=5000.0,
        expenses=3000.0,
        nw_by_month={
            "2026-02": 100_000.0,  # anchor
            "2026-03": 102_500.0,
            "2026-04": 104_000.0,
            "2026-05": 107_000.0,
        },
        flow_by_month={
            "2026-03": (5500.0, 3000.0),  # actual net +2500
            "2026-04": (5000.0, 3500.0),  # actual net +1500
            "2026-05": (5000.0, 2000.0),  # actual net +3000
        },
    )
    out = fs.scorecard("3M", today=date(2026, 6, 6))
    assert out["forecastable"] is True
    assert out["projected_monthly_net"] == 2000.0
    assert [p["year_month"] for p in out["periods"]] == ["2026-03", "2026-04", "2026-05"]

    p_mar = out["periods"][0]
    assert p_mar["cashflow_projected"] == 2000.0
    assert p_mar["cashflow_actual"] == 2500.0
    assert p_mar["cashflow_delta"] == 500.0
    # Net-worth projection: anchor 100000 + 2000/mo cumulative → Mar 102000, actual 102500 → +500
    assert p_mar["net_worth_projected"] == 102_000.0
    assert p_mar["net_worth_actual"] == 102_500.0
    assert p_mar["net_worth_delta"] == 500.0

    # Apr: projected 104000 (100000 + 2*2000), actual 104000 → delta 0
    p_apr = out["periods"][1]
    assert p_apr["net_worth_projected"] == 104_000.0
    assert p_apr["net_worth_delta"] == 0.0

    # cashflow bias = mean(+500, -500, +1000) = +333.33 (slight under-forecast on average)
    assert out["cashflow_accuracy"]["periods_scored"] == 3
    assert out["cashflow_accuracy"]["bias"] == pytest.approx(333.33, abs=0.01)
    assert out["net_worth_accuracy"]["periods_scored"] == 3


def test_scorecard_unanchored_net_worth_nulls_nw_projection(monkeypatch):
    # No net-worth point at the anchor month → net_worth_projected stays None for every period,
    # net_worth_accuracy scores nothing, but cash-flow scoring still works.
    _drive(
        monkeypatch,
        income=4000.0,
        expenses=1000.0,
        nw_by_month={"2026-05": 50_000.0},  # only the last scored month has a point (no anchor)
        flow_by_month={"2026-05": (4000.0, 1500.0)},
    )
    out = fs.scorecard("3M", today=date(2026, 6, 6))
    assert out["forecastable"] is True
    assert out["data_quality"]["net_worth_anchored"] is False
    may = [p for p in out["periods"] if p["year_month"] == "2026-05"][0]
    assert may["net_worth_projected"] is None
    assert may["net_worth_delta"] is None
    assert may["cashflow_actual"] == 2500.0  # 4000 - 1500
    assert out["net_worth_accuracy"]["periods_scored"] == 0
    assert out["cashflow_accuracy"]["periods_scored"] >= 1


def test_scorecard_partial_baseline_is_forecastable(monkeypatch):
    # Income history but no expense history → baseline still exists (expenses default 0).
    _drive(
        monkeypatch,
        income=5000.0,
        expenses=None,
        nw_by_month={"2026-04": 10_000.0, "2026-05": 15_000.0},
        flow_by_month={"2026-05": (5000.0, 0.0)},
    )
    out = fs.scorecard("1M", today=date(2026, 6, 6))
    assert out["forecastable"] is True
    assert out["projected_monthly_net"] == 5000.0
    assert out["projected_monthly_expenses"] is None
