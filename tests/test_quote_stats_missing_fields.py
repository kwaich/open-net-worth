"""Missing provider fields stay absent — a 0 from Yahoo is "no data", never a price of nothing.

Persona QA round 7: VWRA.L's page showed Open $0, Day range $0-$0, 52W Range $0-$202.3 and
Dividend yield 0.00% where Yahoo had returned zeros for an LSE ETF.
"""

from __future__ import annotations

from backend.services import prices

LSE_ETF_INFO = {
    "previousClose": 118.4,
    "regularMarketPreviousClose": 118.4,
    "open": 0,
    "regularMarketOpen": 0,
    "dayLow": 0,
    "dayHigh": 0,
    "fiftyTwoWeekLow": 0,
    "fiftyTwoWeekHigh": 202.3,
    "volume": 0,
    "trailingAnnualDividendYield": 0,
    "dividendYield": 0,
    "trailingPE": 0,
}


def _stats_for(monkeypatch, info: dict) -> dict:
    prices._stats_cache.clear()

    class _Ticker:
        def __init__(self, _symbol: str) -> None:
            self.info = info

    monkeypatch.setattr(prices.yf, "Ticker", _Ticker)
    return prices.fetch_quote_stats("VWRA.L")


def test_zero_valued_fields_are_absent(monkeypatch):
    stats = _stats_for(monkeypatch, LSE_ETF_INFO)
    assert stats == {"prev_close": 118.4, "week52_high": 202.3}


def test_regular_market_fallback_still_fills_a_gap(monkeypatch):
    stats = _stats_for(
        monkeypatch,
        {"open": 0, "regularMarketOpen": 119.1, "dayLow": None, "regularMarketDayLow": 118.0},
    )
    assert stats == {"open": 119.1, "day_low": 118.0}


def test_nan_and_infinite_fields_are_absent(monkeypatch):
    stats = _stats_for(
        monkeypatch,
        {"marketCap": float("nan"), "trailingEps": "Infinity", "trailingPE": 24.5},
    )
    assert stats == {"pe_ratio": 24.5}
