"""
Unit tests for services.daily_total_value (sys-daily-series lane).

Tests pin:
  - income overlay: a single dividend raises income_cum_base and tr_index, value_base unchanged.
  - DRP vs cash dividend produce the SAME tr_index step (no double-count convention).
  - daily_return: null on first point, null when prev tr_index <= 0, formula for interior.
  - FX recompute: changing rates shifts income contribution and value_base.
  - Empty book: series=[], has_real_daily_history=False, sufficient=False.
  - Characterization: zero income events → income_cum_base=0, tr_index derived from value only.

Run:
    .venv/bin/python3 -m pytest tests/test_daily_total_value.py -q
"""

from __future__ import annotations

import pytest

from backend.services import daily_total_value as dtv

# USD pinned at par with the SGD base: these tests exercise the income overlay's mechanics,
# not conversion, and a missing pair is refused rather than assumed 1:1.
PAR_RATES = {"USD": 1.0, "SGD": 1.0}

# ── Helpers to build minimal inject-able primitives ───────────────────────────


def _primitive(dates_values: list[tuple[str, float]]) -> dict:
    """Build a minimal portfolio_value_series result for injection."""
    series = [{"date": d, "value_base": v, "classes": {"equity": v}} for d, v in dates_values]
    return {
        "series": series,
        "data_quality": {
            "market_since": dates_values[0][0] if dates_values else None,
            "inception": dates_values[0][0] if dates_values else None,
            "tiers": {},
            "has_unrecorded_risk": False,
            "has_unknown_cost": False,
            "fx_approximate": False,
        },
    }


def _event(paid_at: str, gross: float, ccy: str = "USD", is_drp: bool = False) -> dict:
    return {
        "id": f"ev-{paid_at}",
        "paid_at": paid_at,
        "gross_amount": gross,
        "tax_withheld": 0.0,
        "currency": ccy,
        "is_drp": is_drp,
    }


# ── Income overlay ─────────────────────────────────────────────────────────────


def test_single_dividend_raises_income_cum_base():
    """A $100 dividend on day 2 raises income_cum_base from day 2 onward; value_base unchanged."""
    prim = _primitive([("2026-05-01", 1000.0), ("2026-05-02", 1005.0), ("2026-05-03", 1010.0)])
    evs = [_event("2026-05-02", 100.0)]
    rates = PAR_RATES
    result = dtv.total_value_series(
        "2026-05-01",
        "2026-05-03",
        income_events=evs,
        primitive_result=prim,
        rates=rates,
    )
    s = result["series"]
    assert s[0]["income_cum_base"] == 0.0
    assert s[1]["income_cum_base"] == pytest.approx(100.0)
    assert s[2]["income_cum_base"] == pytest.approx(100.0)
    # value_base is untouched — it's the price-only primitive passthrough.
    assert s[0]["value_base"] == pytest.approx(1000.0)
    assert s[1]["value_base"] == pytest.approx(1005.0)
    assert s[2]["value_base"] == pytest.approx(1010.0)


def test_drp_and_cash_dividend_same_tr_index_step():
    """DRP and cash dividends produce the SAME tr_index step at paid_at (no double-count).

    The RFC Q1 convention: both are counted in income_cum_base at paid_at. The DRP shares'
    growth flows through value_base afterward. We verify that is_drp=True vs is_drp=False
    for the same gross amount produces identical tr_index on the pay date.
    """
    prim = _primitive([("2026-05-01", 1000.0), ("2026-05-02", 1010.0)])
    rates = PAR_RATES

    cash_ev = [_event("2026-05-02", 50.0, is_drp=False)]
    drp_ev = [_event("2026-05-02", 50.0, is_drp=True)]

    cash_result = dtv.total_value_series(
        "2026-05-01", "2026-05-02", income_events=cash_ev, primitive_result=prim, rates=rates
    )
    drp_result = dtv.total_value_series(
        "2026-05-01", "2026-05-02", income_events=drp_ev, primitive_result=prim, rates=rates
    )

    cash_tr = cash_result["series"][1]["tr_index"]
    drp_tr = drp_result["series"][1]["tr_index"]
    assert cash_tr == pytest.approx(drp_tr)


# ── tr_index and daily_return arithmetic ─────────────────────────────────────


def test_daily_return_null_on_first_point():
    """daily_return is None on the first series point (no previous index to divide by)."""
    prim = _primitive([("2026-05-01", 1000.0), ("2026-05-02", 1050.0)])
    result = dtv.total_value_series(
        "2026-05-01", "2026-05-02", income_events=[], primitive_result=prim, rates=PAR_RATES
    )
    assert result["series"][0]["daily_return"] is None


def test_daily_return_formula_correct():
    """Interior daily_return = tr_index_t / tr_index_{t-1} - 1, verified to 6dp."""
    prim = _primitive([("2026-05-01", 1000.0), ("2026-05-02", 1050.0)])
    result = dtv.total_value_series(
        "2026-05-01", "2026-05-02", income_events=[], primitive_result=prim, rates=PAR_RATES
    )
    s = result["series"]
    idx0 = s[0]["tr_index"]
    idx1 = s[1]["tr_index"]
    expected_dr = idx1 / idx0 - 1.0
    assert s[1]["daily_return"] == pytest.approx(expected_dr, abs=1e-6)
    # 1050/1000 = 1.05 → return = 5%
    assert s[1]["daily_return"] == pytest.approx(0.05, abs=1e-6)


def test_daily_return_null_when_prev_tr_index_zero():
    """daily_return stays None on the second point when the first value_base is 0."""
    prim = _primitive([("2026-05-01", 0.0), ("2026-05-02", 100.0)])
    result = dtv.total_value_series(
        "2026-05-01", "2026-05-02", income_events=[], primitive_result=prim, rates=PAR_RATES
    )
    # base_level is set at first positive total → day 2 is the base; day 1 tr_index is None.
    s = result["series"]
    assert s[0]["tr_index"] is None
    assert s[0]["daily_return"] is None
    # Day 2 is the first real point → daily_return still None (no prior non-null index).
    assert s[1]["daily_return"] is None


def test_tr_index_steps_with_income():
    """tr_index = (value_base + income_cum) / base_level at each point."""
    prim = _primitive([("2026-05-01", 1000.0), ("2026-05-02", 1000.0)])
    evs = [_event("2026-05-02", 100.0)]
    result = dtv.total_value_series(
        "2026-05-01", "2026-05-02", income_events=evs, primitive_result=prim, rates=PAR_RATES
    )
    s = result["series"]
    base_level = 1000.0  # first positive total
    assert s[0]["tr_index"] == pytest.approx(1000.0 / base_level)
    assert s[1]["tr_index"] == pytest.approx((1000.0 + 100.0) / base_level)


# ── FX recompute ───────────────────────────────────────────────────────────────


def test_fx_recompute_shifts_income_contribution():
    """With rates={EUR: 1.2}, a EUR 100 dividend contributes 120 SGD to income_cum_base."""
    prim = _primitive([("2026-05-01", 1000.0), ("2026-05-02", 1000.0)])
    # Override data_quality to reflect fx_approximate
    prim["data_quality"]["fx_approximate"] = True
    evs = [
        {
            "id": "ev1",
            "paid_at": "2026-05-02",
            "gross_amount": 100.0,
            "tax_withheld": 0.0,
            "currency": "EUR",
            "is_drp": False,
        }
    ]
    rates = {"EUR": 1.2, "SGD": 1.0}  # base = SGD (1 EUR = 1.2 SGD)

    result = dtv.total_value_series(
        "2026-05-01", "2026-05-02", income_events=evs, primitive_result=prim, rates=rates
    )
    s = result["series"]
    # 100 EUR × 1.2 rate = 120 SGD in income_cum_base at day 2
    assert s[1]["income_cum_base"] == pytest.approx(120.0, abs=0.01)
    assert result["data_quality"]["fx_approximate"] is True


# ── Empty book ────────────────────────────────────────────────────────────────


def test_empty_book_series_empty_has_real_history_false():
    """Empty primitive → series=[], has_real_daily_history=False, sufficient=False (never 500)."""
    prim = {
        "series": [],
        "data_quality": {
            "market_since": None,
            "inception": None,
            "tiers": {},
            "has_unrecorded_risk": False,
            "has_unknown_cost": False,
            "fx_approximate": False,
        },
    }
    result = dtv.total_value_series(
        "2026-05-01", "2026-05-31", income_events=[], primitive_result=prim, rates={}
    )
    assert result["series"] == []
    assert result["data_quality"]["has_real_daily_history"] is False
    assert result["data_quality"]["sufficient"] is False


def test_single_point_has_real_history_false():
    """One data point is below MIN_VALUE_POINTS → has_real_daily_history=False."""
    prim = _primitive([("2026-05-01", 1000.0)])
    result = dtv.total_value_series(
        "2026-05-01", "2026-05-01", income_events=[], primitive_result=prim, rates={}
    )
    assert result["data_quality"]["has_real_daily_history"] is False


def test_two_points_has_real_history_true():
    """Two distinct price-backed dates → has_real_daily_history=True."""
    prim = _primitive([("2026-05-01", 1000.0), ("2026-05-02", 1010.0)])
    result = dtv.total_value_series(
        "2026-05-01", "2026-05-02", income_events=[], primitive_result=prim, rates={}
    )
    assert result["data_quality"]["has_real_daily_history"] is True


# ── Characterization: zero income events ──────────────────────────────────────


def test_characterization_zero_income_reduces_to_price_series():
    """With no income events, income_cum_base is 0 everywhere and tr_index = value/base_level."""
    dates_values = [
        ("2026-05-01", 1000.0),
        ("2026-05-02", 1020.0),
        ("2026-05-03", 990.0),
    ]
    prim = _primitive(dates_values)
    result = dtv.total_value_series(
        "2026-05-01", "2026-05-03", income_events=[], primitive_result=prim, rates=PAR_RATES
    )
    s = result["series"]
    for pt in s:
        assert pt["income_cum_base"] == pytest.approx(0.0)
    # tr_index is entirely driven by price change.
    base_level = 1000.0
    assert s[0]["tr_index"] == pytest.approx(1000.0 / base_level)
    assert s[1]["tr_index"] == pytest.approx(1020.0 / base_level)
    assert s[2]["tr_index"] == pytest.approx(990.0 / base_level)


# ── Sparkline ─────────────────────────────────────────────────────────────────


def test_sparkline_series_shape(monkeypatch):
    """sparkline_series returns the expected shape with compact fields only."""
    monkeypatch.setattr(
        "backend.services.daily_total_value.total_value_series",
        lambda *a, **k: {
            "base_currency": "SGD",
            "grain": "daily",
            "start": "2026-05-22",
            "end": "2026-05-23",
            "series": [
                {
                    "date": "2026-05-22",
                    "value_base": 1000.0,
                    "income_cum_base": 0.0,
                    "tr_index": 1.0,
                    "daily_return": None,
                    "classes": {},
                },
                {
                    "date": "2026-05-23",
                    "value_base": 1010.0,
                    "income_cum_base": 0.0,
                    "tr_index": 1.01,
                    "daily_return": 0.01,
                    "classes": {},
                },
            ],
            "data_quality": {
                "has_real_daily_history": True,
                "market_since": "2026-05-22",
                "inception": "2026-05-22",
                "value_points": 2,
                "sufficient": True,
                "fx_approximate": False,
                "has_unrecorded_risk": False,
                "tiers": {},
            },
        },
    )
    result = dtv.sparkline_series(days=30)
    assert "series" in result
    assert "data_quality" in result
    assert result["data_quality"]["has_real_daily_history"] is True
    for pt in result["series"]:
        assert set(pt.keys()) == {"date", "value_base", "income_cum_base"}


def test_sparkline_empty_has_real_history_false(monkeypatch):
    """Empty sparkline → has_real_daily_history=False (never 500, never synthetic)."""
    monkeypatch.setattr(
        "backend.services.daily_total_value.total_value_series",
        lambda *a, **k: {
            "base_currency": "SGD",
            "grain": "daily",
            "start": "2026-05-01",
            "end": "2026-05-31",
            "series": [],
            "data_quality": {
                "has_real_daily_history": False,
                "market_since": None,
                "inception": None,
                "value_points": 0,
                "sufficient": False,
                "fx_approximate": False,
                "has_unrecorded_risk": False,
                "tiers": {},
            },
        },
    )
    result = dtv.sparkline_series(days=30)
    assert result["series"] == []
    assert result["data_quality"]["has_real_daily_history"] is False
