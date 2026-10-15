"""Regression: a NaN live quote must never reach the JSON response.

yfinance ``fast_info`` returns NaN for sparse data (a fresh IPO / SPAC like SPCX, an
illiquid or halted ticker). NaN is truthy, so it used to sail past ``if price:`` into the
live-price map; the held position then computed ``qty * NaN`` and the asset-detail
endpoint 500'd at Starlette's ``json.dumps(content, allow_nan=False)``.

``_fetch_one`` must treat a non-finite quote as missing — dropping it lets the caller fall
back to the recorded close, so the position and headline price stay finite and the payload
serializes. Pure + offline: yfinance is monkeypatched, no network and no data dir.
"""

import json
import math

from backend.services import prices
from backend.services.prices import _fetch_one, _finite, _quote_to_price


class _EmptyHist:
    def __len__(self):
        return 0


class _FakeTicker:
    """Stands in for ``yf.Ticker`` — serves a canned ``fast_info`` and an empty history."""

    def __init__(self, symbol, fast):
        self._fast = fast

    @property
    def fast_info(self):
        return self._fast

    def history(self, period="2d"):
        return _EmptyHist()


def _patch(monkeypatch, fast):
    monkeypatch.setattr(prices.yf, "Ticker", lambda s: _FakeTicker(s, fast))


def test_finite_coerces_non_finite_to_none():
    assert _finite(192.5) == 192.5
    assert _finite(0.0) == 0.0  # a real $0 quote is finite, kept (caller's own guard drops it)
    assert _finite(float("nan")) is None
    assert _finite(float("inf")) is None
    assert _finite(float("-inf")) is None
    assert _finite(None) is None
    assert _finite("not-a-number") is None


def test_fetch_one_drops_nan_quote(monkeypatch):
    # SPCX's real shape: fast_info returns NaN, no 2d history to fall back to.
    _patch(monkeypatch, {"lastPrice": float("nan"), "previousClose": float("nan")})
    sym, price, prev = _fetch_one("SPCX")
    assert sym == "SPCX"
    assert price is None and prev is None  # NaN treated as missing, not emitted as a quote
    # The whole point: whatever we return must be JSON-safe under allow_nan=False.
    json.dumps({"prices": {sym: price}, "prev": {sym: prev}}, allow_nan=False)


def test_fetch_one_keeps_finite_price_drops_nan_prev(monkeypatch):
    _patch(monkeypatch, {"lastPrice": 192.5, "previousClose": float("nan")})
    sym, price, prev = _fetch_one("SPCX")
    assert price == 192.5
    assert prev is None  # a NaN previous-close is dropped, never paired with a good last price
    assert prev is None or math.isfinite(prev)
    json.dumps({"last": price, "prev": prev}, allow_nan=False)


def test_fetch_one_rejects_a_regularMarketPreviousClose_that_mirrors_the_live_price(monkeypatch):
    # KSTL.DE right at the XETRA open (owner-reproduced live, 2026-09-21): fast_info's
    # own `previousClose` is momentarily absent, and `regularMarketPreviousClose` lags —
    # it echoes the CURRENT price (36.46) instead of the real prior close (36.61), which
    # Yahoo's own UI and `previousClose` itself (seconds later) both agreed on. Treating
    # that echo as a genuine previous close fed a "Prev close: 36.46" pill identical to
    # the live price — a wrong number stated with total confidence, worse than "unknown".
    _patch(
        monkeypatch,
        {"lastPrice": 36.46, "previousClose": None, "regularMarketPreviousClose": 36.46},
    )
    sym, price, prev = _fetch_one("KSTL.DE")
    assert price == 36.46
    assert prev is None  # suspicious echo dropped, not surfaced as a real previous close


def test_fetch_one_still_uses_regularMarketPreviousClose_when_it_genuinely_differs(monkeypatch):
    # The fallback exists for a real reason (previousClose sometimes just isn't populated
    # at all) — only the SUSPICIOUS "identical to the live price" case is rejected.
    _patch(
        monkeypatch,
        {"lastPrice": 36.46, "previousClose": None, "regularMarketPreviousClose": 36.61},
    )
    sym, price, prev = _fetch_one("KSTL.DE")
    assert price == 36.46
    assert prev == 36.61


# _quote_to_price: the v7 batch-quote path's OWN equivalent of _fetch_one above — the one
# KSTL.DE actually resolves through live (owner-reproduced 2026-09-21: fetch_live() itself,
# not just fast_info in isolation, returned prev_prices={"KSTL.DE": 36.46} — the SAME wrong
# echo, because this function checked regularMarketPreviousClose FIRST, not as a fallback).


def test_quote_to_price_prefers_previousClose_over_regularMarketPreviousClose():
    # Both present and genuinely different — previousClose (the reliable field) wins, not
    # whichever happens to be checked first.
    price, prev = _quote_to_price(
        {"regularMarketPrice": 36.46, "previousClose": 36.61, "regularMarketPreviousClose": 36.46}
    )
    assert price == 36.46
    assert prev == 36.61


def test_quote_to_price_rejects_a_regularMarketPreviousClose_that_mirrors_the_live_price():
    # KSTL.DE's actual live shape right at the XETRA open: previousClose absent,
    # regularMarketPreviousClose echoing the current price instead of the real 36.61.
    price, prev = _quote_to_price(
        {"regularMarketPrice": 36.46, "previousClose": None, "regularMarketPreviousClose": 36.46}
    )
    assert price == 36.46
    assert prev is None  # suspicious echo dropped, not surfaced as a real previous close


def test_quote_to_price_still_uses_regularMarketPreviousClose_when_it_genuinely_differs():
    price, prev = _quote_to_price(
        {"regularMarketPrice": 36.46, "previousClose": None, "regularMarketPreviousClose": 36.61}
    )
    assert price == 36.46
    assert prev == 36.61


def test_quote_to_price_drops_nan_previous_close_without_reviving_the_fallback():
    # previousClose present but non-finite (NaN) is EXPLICIT bad data from Yahoo, not "absent"
    # — the whole quote is treated as unusable, same as the pre-existing NaN-guard contract.
    price, prev = _quote_to_price(
        {
            "regularMarketPrice": 36.46,
            "previousClose": float("nan"),
            "regularMarketPreviousClose": 36.61,
        }
    )
    assert price is None
    assert prev is None
