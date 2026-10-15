"""Unit tests for backend.services.portfolio_drivers — top gainers/losers for today.

Offline: hydrated_positions() is monkeypatched, no store/network.
"""

from __future__ import annotations

from backend.services import portfolio_drivers


def _row(
    ticker: str,
    day_change_base: float,
    price: float = 100.0,
    vesting_state: str = "vested",
) -> dict:
    return {
        "ticker": ticker,
        "label": ticker,
        "instrument_id": ticker,
        "price": price,
        "day_change": day_change_base,
        "day_change_pct": (day_change_base / price) * 100,
        "quote_currency": "SGD",
        "vesting_state": vesting_state,
    }


def test_gainers_and_losers_split_and_sorted(monkeypatch):
    rows = [_row("A", 10), _row("B", 30), _row("C", -5), _row("D", -20)]
    monkeypatch.setattr(portfolio_drivers, "hydrated_positions", lambda: rows)
    monkeypatch.setattr(portfolio_drivers, "read_profile", lambda: {"base_currency": "SGD"})
    monkeypatch.setattr(portfolio_drivers, "load_fx_cache", lambda: {"SGD": 1.0})

    out = portfolio_drivers.get_drivers(limit_each=3)
    assert [g["ticker"] for g in out["top_gainers"]] == ["B", "A"]
    assert [ell["ticker"] for ell in out["top_losers"]] == ["D", "C"]


def test_unvested_equity_grant_units_never_appear_as_a_driver(monkeypatch):
    """An unvested RSU tranche is not owned yet (ADR
    2026-07-31-unvested-equity-excluded-from-net-worth) — its paper day-move must never
    appear in "Today's Drivers", however large, since the holder cannot act on it."""
    rows = [
        _row("AAPL", 10),
        _row("RSU", 500000, price=1000000, vesting_state="unvested"),
    ]
    monkeypatch.setattr(portfolio_drivers, "hydrated_positions", lambda: rows)
    monkeypatch.setattr(portfolio_drivers, "read_profile", lambda: {"base_currency": "SGD"})
    monkeypatch.setattr(portfolio_drivers, "load_fx_cache", lambda: {"SGD": 1.0})

    out = portfolio_drivers.get_drivers(limit_each=3)
    all_tickers = {g["ticker"] for g in out["top_gainers"]} | {
        ell["ticker"] for ell in out["top_losers"]
    }
    assert "RSU" not in all_tickers
    assert out["top_gainers"] == [
        {
            "ticker": "AAPL",
            "label": "AAPL",
            "instrument_id": "AAPL",
            "day_change_base": 10.0,
            "day_change_pct": 10.0,
        }
    ]
