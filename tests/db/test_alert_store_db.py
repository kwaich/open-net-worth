"""AlertStore on BookPayloadTableStore (plan 4d, task 2) — DB-CRUD round trip plus validation
parity (ticker/op/value normalization) and the by_ticker grouping domain case.
"""

from __future__ import annotations

import pytest

from backend.core.errors import ValidationError
from backend.db.alert_store import AlertStore
from backend.db.instrument_store import InstrumentStore


def test_create_round_trip_normalizes_fields(db_engine):
    store = AlertStore(engine=db_engine)
    row = store.create({"ticker": "aapl", "op": ">=", "value": "150.5"})
    assert row["ticker"] == "AAPL"
    assert row["value"] == 150.5
    assert store.find_by_id(row["id"])["op"] == ">="


def test_create_rejects_bad_ticker_op_value(db_engine):
    store = AlertStore(engine=db_engine)
    with pytest.raises(ValidationError):
        store.create({"ticker": "", "op": ">=", "value": 1})
    with pytest.raises(ValidationError):
        store.create({"ticker": "AAPL", "op": "~=", "value": 1})
    with pytest.raises(ValidationError):
        store.create({"ticker": "AAPL", "op": ">=", "value": "not-a-number"})


def test_update_patches_whitelisted_fields_only(db_engine):
    store = AlertStore(engine=db_engine)
    row = store.create({"ticker": "AAPL", "op": ">=", "value": 100})
    updated = store.update(row["id"], {"value": 200, "note": "watch this", "ticker": "MSFT"})
    assert updated["value"] == 200.0
    assert updated["note"] == "watch this"
    assert updated["ticker"] == "AAPL"  # not in the whitelist — unchanged


# ── instrument_id backfill at create time (identity-migration finding, 2026-08-30) ──────────


def test_create_backfills_instrument_id_from_ticker(db_engine):
    inst = InstrumentStore(engine=db_engine).create(
        {
            "symbol": "AAPL",
            "asset_class": "equity",
            "quote_currency": "USD",
            "price_source": "yahoo",
            "price_source_id": "AAPL",
        }
    )
    store = AlertStore(engine=db_engine)
    row = store.create({"ticker": "aapl", "op": ">=", "value": 100})
    assert row["instrument_id"] == inst["id"]


def test_create_leaves_instrument_id_blank_when_ticker_unresolved(db_engine):
    store = AlertStore(engine=db_engine)
    row = store.create({"ticker": "ZZZZ", "op": ">=", "value": 100})
    assert row["instrument_id"] == ""


def test_create_keeps_caller_supplied_instrument_id(db_engine):
    store = AlertStore(engine=db_engine)
    row = store.create(
        {"ticker": "AAPL", "op": ">=", "value": 100, "instrument_id": "inst_explicit"}
    )
    assert row["instrument_id"] == "inst_explicit"


# ── Domain parity: for_ticker / by_ticker grouping ──────────────────────────────────────────


def test_for_ticker_and_by_ticker_grouping(db_engine):
    store = AlertStore(engine=db_engine)
    store.create({"ticker": "aapl", "op": ">=", "value": 100})
    store.create({"ticker": "AAPL", "op": "<=", "value": 90})
    store.create({"ticker": "msft", "op": ">=", "value": 300})

    assert len(store.for_ticker("aapl")) == 2

    grouped = store.by_ticker()
    assert set(grouped.keys()) == {"AAPL", "MSFT"}
    assert len(grouped["AAPL"]) == 2 and len(grouped["MSFT"]) == 1
