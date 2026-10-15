"""A saved price trigger's bare ticker is pinned to an instrument, so its currency is known."""

from __future__ import annotations

from backend.services.automations import price_symbols
from backend.services.automations.price_symbols import pin_price_instrument


def _price(symbol: str) -> dict:
    return {
        "kind": "event",
        "event": "price",
        "when": {"symbol": symbol, "direction": "above", "value": 300.0},
        "limit": "day",
    }


class _Store:
    def __init__(self, known: set[str]):
        self.known = known

    def find_by_id(self, instrument_id: str):
        return {"id": instrument_id} if instrument_id in self.known else None


def test_an_unknown_ticker_is_resolved_to_an_instrument(monkeypatch):
    monkeypatch.setattr(price_symbols, "InstrumentStore", lambda: _Store(set()))
    import backend.services.instruments as instruments

    monkeypatch.setattr(
        instruments, "resolve_instrument", lambda q: ({"id": "inst_tsla", "symbol": q}, True)
    )
    assert pin_price_instrument(_price("TSLA"))["when"]["symbol"] == "inst_tsla"


def test_an_instrument_id_is_left_alone(monkeypatch):
    monkeypatch.setattr(price_symbols, "InstrumentStore", lambda: _Store({"inst_aapl"}))
    assert pin_price_instrument(_price("inst_aapl")) == _price("inst_aapl")


def test_a_failed_lookup_keeps_the_ticker_as_typed(monkeypatch):
    monkeypatch.setattr(price_symbols, "InstrumentStore", lambda: _Store(set()))
    import backend.services.instruments as instruments

    def boom(q):
        raise OSError("offline")

    monkeypatch.setattr(instruments, "resolve_instrument", boom)
    assert pin_price_instrument(_price("TSLA")) == _price("TSLA")
    monkeypatch.setattr(instruments, "resolve_instrument", lambda q: (None, False))
    assert pin_price_instrument(_price("ZZNOPE")) == _price("ZZNOPE")


def test_other_triggers_are_untouched():
    txn = {"kind": "event", "event": "txn", "when": {}, "limit": "each"}
    assert pin_price_instrument(txn) is txn
