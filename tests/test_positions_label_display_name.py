"""Regression: the hydrated row `label` is the cleaned display name.

The "Today's movers" / holdings surfaces render `row.label`, so the security-type
suffix ("… Class A Common Stock") must be stripped server-side — the user was still
seeing the full Yahoo longName because stripping only ever existed in one frontend
view. Stripping is display-only: the instrument's stored `name` is never mutated.

Offline: stores are monkeypatched (mirrors tests/test_positions_hydrated_zero_qty.py).
"""

from __future__ import annotations

import pytest

from backend.db.account_store import AccountStore
from backend.db.instrument_store import InstrumentStore
from backend.db.position_store import PositionStore
from backend.db.price_history_store import PriceHistoryStore
from backend.services import positions as positions_service

_INSTRUMENTS = [
    {
        "id": "inst-spcx",
        "symbol": "SPCX",
        "name": "Space Exploration Technologies Corp. Class A Common Stock",
        "asset_class": "equity",
        "quote_currency": "USD",
        "price_source": "yahoo",
        "price_source_id": "SPCX",
        "quote_per_unit": 1.0,
    }
]
_ACCOUNTS = [{"id": "acc-1", "name": "Brokerage", "platform": "Brokerage", "currency": "USD"}]
_POS = {
    "id": "pos-1",
    "instrument_id": "inst-spcx",
    "account_id": "acc-1",
    "quantity": 5.0,
    "cost_basis": 1000.0,
    "cost_currency": "USD",
    "bucket": "liquid",
}


@pytest.fixture
def patched_stores(monkeypatch):
    instruments = [dict(_INSTRUMENTS[0])]
    monkeypatch.setattr(PositionStore, "all", lambda self: [dict(_POS)])
    monkeypatch.setattr(InstrumentStore, "all", lambda self: instruments)
    monkeypatch.setattr(AccountStore, "all", lambda self: list(_ACCOUNTS))
    monkeypatch.setattr(PriceHistoryStore, "latest_map", lambda self: {"SPCX": 203.435})
    monkeypatch.setattr(PriceHistoryStore, "prior_to_map", lambda self: {})
    return instruments


def test_hydrated_label_strips_security_suffix(patched_stores):
    (row,) = positions_service.hydrated()
    assert row["label"] == "Space Exploration Technologies Corp."


def test_stored_instrument_name_is_untouched(patched_stores):
    positions_service.hydrated()
    # Display-only: the full legal name remains on the instrument record.
    assert patched_stores[0]["name"] == "Space Exploration Technologies Corp. Class A Common Stock"
