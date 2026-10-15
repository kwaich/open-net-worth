"""prices.fetch_quote_snapshots — compact watch-engine view of a raw Yahoo v7 quote."""

from __future__ import annotations

import backend.services.prices as prices


def test_snapshot_keeps_only_fields_yahoo_returned():
    snap = prices._snapshot_from_quote(
        {
            "regularMarketPrice": 105.0,
            "previousClose": 100.0,
            "fiftyTwoWeekHigh": 120.0,
            "twoHundredDayAverage": 95.5,
            "averageDailyVolume3Month": 1_000_000,
            "regularMarketVolume": 2_000_000,
            "earningsTimestamp": 1_900_000_000,
            "shortName": "Test Co",
            "currency": "USD",
        }
    )
    assert snap["price"] == 105.0 and snap["prev_close"] == 100.0
    assert snap["week52_high"] == 120.0 and snap["avg_200d"] == 95.5
    assert snap["earnings_at"].startswith("2030-")
    assert "week52_low" not in snap and "avg_50d" not in snap


def test_snapshot_is_none_without_a_usable_price():
    assert prices._snapshot_from_quote({"regularMarketPrice": 0}) is None
    assert prices._snapshot_from_quote({}) is None


def test_garbage_numbers_are_dropped_not_zeroed():
    snap = prices._snapshot_from_quote(
        {"regularMarketPrice": 10.0, "fiftyTwoWeekHigh": "n/a", "earningsTimestamp": "soon"}
    )
    assert "week52_high" not in snap and "earnings_at" not in snap


def test_fetch_returns_empty_under_qa_mode_and_for_no_symbols(monkeypatch):
    class _QA:
        onw_qa_mode = True

    def boom(_symbols):
        raise AssertionError("QA mode must never reach the network")

    monkeypatch.setattr(prices, "get_settings", lambda: _QA())
    monkeypatch.setattr(prices, "_yf_batch_quote", boom)
    assert prices.fetch_quote_snapshots([]) == {}
    assert prices.fetch_quote_snapshots(["NVDA"]) == {}


def test_fetch_batches_and_caches(monkeypatch):
    calls: list[list[str]] = []

    def fake_batch(symbols):
        calls.append(list(symbols))
        return {s: {"regularMarketPrice": 10.0, "previousClose": 9.0} for s in symbols}

    class _S:
        onw_qa_mode = False

    monkeypatch.setattr(prices, "get_settings", lambda: _S())
    monkeypatch.setattr(prices, "_yf_batch_quote", fake_batch)
    monkeypatch.setattr(prices, "_snapshot_cache", {})
    first = prices.fetch_quote_snapshots(["AAA", "BBB", "AAA"])
    second = prices.fetch_quote_snapshots(["AAA", "CCC"])
    assert set(first) == {"AAA", "BBB"} and set(second) == {"AAA", "CCC"}
    assert calls == [["AAA", "BBB"], ["CCC"]]
