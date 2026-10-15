"""
Regression test for INV1-6 — a fully-disposed position must not linger as a $0
ghost row in the displayed/valued projection.

hydrated() used to skip only orphan positions (no instrument), so a holding sold
down to quantity 0 stayed in the table at qty 0 / value 0. It must now be filtered
out of the displayed rows — but the underlying record is KEPT on disk (history /
realized-P&L lineage), so this only asserts the projection, never a delete.

Offline: the stores hydrated() reads (PositionStore / InstrumentStore / AccountStore
/ PriceHistoryStore) are monkeypatched, so no live data file is touched — mirrors
tests/test_char_ledger_math.py.
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
        "id": "inst-1",
        "symbol": "AAA",
        "name": "Alpha Inc",
        "asset_class": "equity",
        "quote_currency": "USD",
        "price_source": "yahoo",
        "price_source_id": "AAA",
        "quote_per_unit": 1.0,
    }
]

_ACCOUNTS = [{"id": "acc-1", "name": "Brokerage", "platform": "Brokerage", "currency": "USD"}]

_POS_OPEN = {
    "id": "pos-open",
    "instrument_id": "inst-1",
    "account_id": "acc-1",
    "quantity": 10.0,
    "cost_basis": 100.0,
    "cost_currency": "USD",
    "bucket": "liquid",
}
_POS_SOLD = {
    "id": "pos-sold",
    "instrument_id": "inst-1",
    "account_id": "acc-1",
    "quantity": 0.0,  # fully disposed
    "cost_basis": 100.0,
    "cost_currency": "USD",
    "bucket": "liquid",
}


@pytest.fixture
def patched_stores(monkeypatch):
    """Point every store hydrated() reads at in-memory fixtures (no live files)."""
    positions = [dict(_POS_OPEN), dict(_POS_SOLD)]
    monkeypatch.setattr(PositionStore, "all", lambda self: positions)
    monkeypatch.setattr(InstrumentStore, "all", lambda self: list(_INSTRUMENTS))
    monkeypatch.setattr(AccountStore, "all", lambda self: list(_ACCOUNTS))
    monkeypatch.setattr(PriceHistoryStore, "latest_map", lambda self: {"AAA": 130.0})
    monkeypatch.setattr(PriceHistoryStore, "prior_to_map", lambda self: {})
    return positions


def test_zero_quantity_position_absent_from_hydrated_rows(patched_stores):
    rows = positions_service.hydrated()
    ids = {r["position_id"] for r in rows}
    # The fully-sold (qty 0) holding is gone from the displayed rows...
    assert "pos-sold" not in ids
    # ...while the still-open holding stays.
    assert "pos-open" in ids
    assert len(rows) == 1


def test_normal_position_still_appears_with_value(patched_stores):
    rows = positions_service.hydrated()
    (row,) = rows
    assert row["position_id"] == "pos-open"
    assert row["quantity"] == 10.0
    assert row["price"] == 130.0


def test_stored_zero_quantity_position_is_untouched(patched_stores):
    """Filtering is display-only — the on-disk record is never mutated/removed."""
    before = [dict(p) for p in patched_stores]
    positions_service.hydrated()
    assert patched_stores == before
    assert any(p["id"] == "pos-sold" and p["quantity"] == 0.0 for p in patched_stores)
