"""Exact return windows and per-holding performance over a window (synthetic data only)."""

from __future__ import annotations

from datetime import date

import pytest

import backend.services.holding_returns as hr
import backend.services.portfolio_history as ph
from backend.core.errors import ValidationError
from backend.services import returns

RATES = {"USD": 1.0}
INSTRUMENTS = {
    "AAA": {
        "id": "AAA",
        "symbol": "AAA",
        "name": "Alpha Corp",
        "asset_class": "equity",
        "price_source_id": "AAA",
        "quote_currency": "USD",
        "quote_per_unit": 1.0,
    },
    "BBB": {
        "id": "BBB",
        "symbol": "BBB",
        "name": "Beta Coin",
        "asset_class": "crypto",
        "price_source_id": "BBB",
        "quote_currency": "USD",
        "quote_per_unit": 1.0,
    },
    "BND": {
        "id": "BND",
        "symbol": "BND",
        "name": "A bond",
        "asset_class": "bond",
        "price_source_id": "BND",
        "quote_currency": "USD",
        "quote_per_unit": 1.0,
    },
}
START, END = "2026-08-31", "2026-09-30"
PRICES = {
    START: {"AAA": 100.0, "BBB": 50.0, "BND": 99.0},
    END: {"AAA": 110.0, "BBB": 45.0, "BND": 99.0},
}


def _lot(iid, qty, acquired_at, unit_cost=None):
    return {
        "id": f"lot-{iid}-{acquired_at}",
        "instrument_id": iid,
        "position_id": f"pos-{iid}",
        "account_id": "acct",
        "acquired_at": acquired_at,
        "original_qty": qty,
        "remaining_qty": qty,
        "unit_cost": unit_cost,
        "cost_currency": "USD",
    }


LOTS = [
    _lot("AAA", 10, "2026-01-05", 80.0),
    _lot("BBB", 20, "2026-01-05", 40.0),
    _lot("BBB", 10, "2026-09-10", 48.0),
    _lot("BND", 5, "2026-01-05", 98.0),
]
DISPOSALS = [
    {
        "id": "d1",
        "instrument_id": "AAA",
        "position_id": "pos-AAA",
        "disposed_at": "2026-09-20",
        "quantity": 4,
        "proceeds_per_unit": 105.0,
        "proceeds_currency": "USD",
        "disposal_fees": 0.0,
    }
]
# The flows the window saw: a 10 x 48 buy of BBB, a 4 x 105 sale of AAA (user-pocket sign).
FLOWS = [("2026-09-10", -480.0, "BBB"), ("2026-09-20", 420.0, "AAA")]


@pytest.fixture(autouse=True)
def _usd_base(monkeypatch):
    monkeypatch.setattr(hr, "base_currency", lambda: "USD")
    monkeypatch.setattr(ph, "base_currency", lambda: "USD")


def _holdings():
    return hr.holding_period_returns(
        START,
        END,
        rates=RATES,
        instruments=INSTRUMENTS,
        price_history=PRICES,
        lots=LOTS,
        disposals=DISPOSALS,
        inception="2026-01-01",
        flows=FLOWS,
    )


def test_each_holding_is_measured_over_the_window_net_of_its_trades():
    rows = {r["symbol"]: r for r in _holdings()}
    # AAA: 10 held at 100 → 6 held at 110, sold 4 for 420: 660 − 1000 + 420 = +80.
    assert rows["AAA"]["start_value_base"] == 1000.0 and rows["AAA"]["end_value_base"] == 660.0
    assert rows["AAA"]["sold_base"] == 420.0 and rows["AAA"]["gain_base"] == 80.0
    assert rows["AAA"]["price_change_pct"] == 10.0
    # BBB: 20 at 50 → 30 at 45, bought 10 for 480: 1350 − 1000 − 480 = −130. The buy is
    # not a gain; the fall in price is the loss.
    assert rows["BBB"]["bought_base"] == 480.0 and rows["BBB"]["gain_base"] == -130.0
    assert rows["BBB"]["price_change_pct"] == -10.0
    # Bonds sit outside the market value series, exactly as in the portfolio figures.
    assert "BND" not in rows
    assert [r["symbol"] for r in _holdings()] == ["AAA", "BBB"]  # best first


def test_return_is_gain_over_the_capital_actually_at_work():
    rows = {r["symbol"]: r for r in _holdings()}
    # BBB: 1000 at work all month + 480 bought on 10 Sep, at work 20 of 30 days → 1320.
    assert rows["BBB"]["return_pct"] == pytest.approx(-130 / 1320 * 100, abs=0.01)
    # AAA: 1000 at work, 420 taken out 10 days before the end → 1000 − 140 = 860.
    assert rows["AAA"]["return_pct"] == pytest.approx(80 / 860 * 100, abs=0.01)


def test_holdings_add_up_to_the_portfolio_over_the_same_window():
    series = ph.portfolio_value_series(
        START,
        END,
        "daily",
        inception="2026-01-01",
        lots=LOTS,
        disposals=DISPOSALS,
        instruments=INSTRUMENTS,
        price_history={d: PRICES[d] for d in PRICES},
        rates=RATES,
    )["series"]
    points = {p["date"]: p["value_base"] for p in series}
    portfolio_gain = points[END] - points[START] + sum(a for _, a, _ in FLOWS)
    rows = _holdings()
    assert sum(r["start_value_base"] for r in rows) == pytest.approx(points[START])
    assert sum(r["end_value_base"] for r in rows) == pytest.approx(points[END])
    assert sum(r["gain_base"] for r in rows) == pytest.approx(portfolio_gain)


def test_a_holding_with_no_capital_at_work_has_no_return_not_zero():
    rows = hr.holding_period_returns(
        START,
        END,
        rates=RATES,
        instruments=INSTRUMENTS,
        price_history=PRICES,
        lots=[],
        disposals=[],
        inception="2026-01-01",
        flows=[(END, 10.0, "AAA")],
    )
    assert rows[0]["return_pct"] is None


def test_a_calendar_month_is_measured_from_the_previous_close():
    today = date(2026, 10, 1)
    assert returns.resolve_window("1M", today, date_from="2026-09-01", date_to="2026-09-30") == (
        "2026-08-31",
        "2026-09-30",
    )
    # date_to in the future is clamped to today; no date_to means up to today.
    assert returns.resolve_window("1M", today, date_from="2026-09-15", date_to="2027-01-01")[1] == (
        "2026-10-01"
    )
    assert returns.resolve_window("1M", today, date_from="2026-09-15") == (
        "2026-09-14",
        "2026-10-01",
    )
    # No range at all keeps the period behaviour.
    assert returns.resolve_window("7D", today) == returns.resolve_period("7D", today)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"date_from": "2026-09-30", "date_to": "2026-09-01"},
        {"date_from": "Sept 1"},
        {"date_to": "2026-09-30"},
        {"date_from": "2026-11-01"},
    ],
)
def test_a_bad_range_is_refused(kwargs):
    with pytest.raises(ValidationError):
        returns.resolve_window("1M", date(2026, 10, 1), **kwargs)


def test_tagged_flows_are_the_same_flows_cash_flows_reports(monkeypatch):
    tagged = [("2026-09-10", -480.0, "BBB"), ("2026-09-20", 420.0, "AAA")]
    monkeypatch.setattr(returns, "tagged_cash_flows", lambda *a, **k: (tagged, False))
    flows, unknown = returns.cash_flows(START, END, "USD", rates=RATES)
    assert flows == [("2026-09-10", -480.0), ("2026-09-20", 420.0)] and unknown is False
