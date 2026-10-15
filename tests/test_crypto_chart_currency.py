"""Asset-detail crypto charts follow quote currency and honest provider states."""

from __future__ import annotations

import inspect

from backend.services import asset_detail as ad
from backend.services import prices


class _FakeInst:
    def __init__(self, inst):
        self._inst = inst

    def find_by_id(self, instrument_id):
        return self._inst if self._inst["id"] == instrument_id else None


def _crypto_inst():
    return {
        "id": "i1",
        "price_source_id": "bitcoin",
        "price_source": "coingecko",
        "quote_currency": "USD",
        "asset_class": "crypto",
    }


def _live_result(points):
    return {
        "provider": "yahoo",
        "state": "live",
        "as_of": points[-1]["date"],
        "degraded_reason": None,
        "points": points,
    }


def test_crypto_chart_uses_quote_currency_and_yahoo_status(monkeypatch):
    monkeypatch.setattr(ad, "InstrumentStore", lambda: _FakeInst(_crypto_inst()))
    captured = {}
    points = [
        {"date": "2026-08-12T00:00:00+00:00", "price": 68000},
        {"date": "2026-08-12T00:01:00+00:00", "price": 68010},
    ]

    def fake_result(canonical_id, timeframe, source, currency):
        captured.update(
            {
                "canonical_id": canonical_id,
                "timeframe": timeframe,
                "source": source,
                "currency": currency,
            }
        )
        return _live_result(points)

    monkeypatch.setattr(prices, "fetch_history_result", fake_result)

    result = ad.price_series("i1", "1D")

    assert captured == {
        "canonical_id": "bitcoin",
        "timeframe": "1D",
        "source": "coingecko",
        "currency": "USD",
    }
    assert result["currency"] == "USD"
    assert result["provider"] == "yahoo"
    assert result["state"] == "live"
    assert result["intraday_available"] is True


def test_equity_chart_keeps_quote_currency(monkeypatch):
    inst = {
        "id": "i2",
        "price_source_id": "AAPL",
        "price_source": "yahoo",
        "quote_currency": "USD",
        "asset_class": "equity",
    }
    monkeypatch.setattr(ad, "InstrumentStore", lambda: _FakeInst(inst))
    captured = {}
    points = [
        {"date": "2026-08-11", "price": 199.0},
        {"date": "2026-08-12", "price": 200.0},
    ]

    def fake_result(_ticker, _timeframe, _source, currency):
        captured["currency"] = currency
        return _live_result(points)

    monkeypatch.setattr(prices, "fetch_history_result", fake_result)

    result = ad.price_series("i2", "1M")

    assert captured == {"currency": "USD"}
    assert result["currency"] == "USD"
    assert result["points"] == points


def test_unavailable_crypto_series_has_no_points_or_intraday(monkeypatch):
    monkeypatch.setattr(ad, "InstrumentStore", lambda: _FakeInst(_crypto_inst()))
    monkeypatch.setattr(
        prices,
        "fetch_history_result",
        lambda *args: {
            "provider": None,
            "state": "unavailable",
            "as_of": None,
            "degraded_reason": "upstream_unavailable",
            "points": [],
        },
    )

    result = ad.price_series("i1", "1D")

    assert result["provider"] is None
    assert result["state"] == "unavailable"
    assert result["points"] == []
    assert result["intraday_available"] is False


def test_unavailable_crypto_series_never_resurrects_unbounded_history(monkeypatch, db_isolated):
    from backend.db.price_history_store import PriceHistoryStore

    PriceHistoryStore().record_many({"bitcoin": 60000.0}, source="live")
    monkeypatch.setattr(ad, "InstrumentStore", lambda: _FakeInst(_crypto_inst()))
    monkeypatch.setattr(
        prices,
        "fetch_history_result",
        lambda *args: {
            "provider": None,
            "state": "unavailable",
            "as_of": None,
            "degraded_reason": "stale_expired",
            "points": [],
        },
    )

    result = ad.price_series("i1", "1M")

    assert result["state"] == "unavailable"
    assert result["points"] == []


def test_crypto_price_block_stays_in_quote_currency():
    block = ad._price_block(
        _crypto_inst(),
        {"prices": {"bitcoin": 100.0}, "prev_prices": {"bitcoin": 80.0}},
        {"SGD": 1.0, "USD": 1.3},
        "SGD",
    )

    assert block["currency"] == "USD"
    assert block["last"] == 100.0
    assert block["change_abs"] == 20.0
    assert block["change_pct"] == 25.0


def test_equity_header_price_stays_quote_currency():
    inst = {
        "id": "i2",
        "price_source_id": "AAPL",
        "price_source": "yahoo",
        "quote_currency": "USD",
        "asset_class": "equity",
    }
    block = ad._price_block(
        inst,
        {"prices": {"AAPL": 200.0}, "prev_prices": {"AAPL": 190.0}},
        {"SGD": 1.0, "USD": 1.3},
        "SGD",
    )

    assert block["currency"] == "USD"
    assert block["last"] == 200.0
    assert block["change_abs"] == 10.0


def test_asset_price_surfaces_have_no_currency_override_parameters():
    assert list(inspect.signature(ad.price_series).parameters) == ["instrument_id", "timeframe"]
    assert list(inspect.signature(ad._price_block).parameters) == ["inst", "live", "fx", "base"]


def test_price_series_does_not_double_fetch_on_unavailable(monkeypatch):
    monkeypatch.setattr(ad, "InstrumentStore", lambda: _FakeInst(_crypto_inst()))
    calls = 0

    def unavailable(*args):
        nonlocal calls
        calls += 1
        return {
            "provider": None,
            "state": "unavailable",
            "as_of": None,
            "degraded_reason": "upstream_unavailable",
            "points": [],
        }

    monkeypatch.setattr(prices, "fetch_history_result", unavailable)

    ad.price_series("i1", "1D")

    assert calls == 1


def test_unavailable_crypto_quote_never_resurrects_unbounded_history(db_isolated):
    from backend.db.price_history_store import PriceHistoryStore

    PriceHistoryStore().record_many({"bitcoin": 60000.0}, source="live")

    block = ad._price_block(
        _crypto_inst(),
        {"prices": {}, "states": {"bitcoin": "unavailable"}},
        {"USD": 1.0},
        "USD",
    )

    assert block["last"] is None
    assert block["state"] == "unavailable"


def test_stale_crypto_quote_block_exposes_live_degraded_reason():
    block = ad._price_block(
        _crypto_inst(),
        {
            "prices": {"bitcoin": 68000.0},
            "states": {"bitcoin": "stale"},
            "degraded_reasons": {"bitcoin": "upstream_unavailable"},
        },
        {"USD": 1.0},
        "USD",
    )

    assert block["state"] == "stale"
    assert block["degraded_reason"] == "upstream_unavailable"
