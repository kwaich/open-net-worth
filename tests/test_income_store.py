"""
CRUD/query tests for IncomeEventStore (P2-INV2) — pattern tests/test_positions_stores.py.

IncomeEventStore is DB-backed (plan 4a task 3) — db_isolated gives each test its own fresh,
migrated database.
"""

from __future__ import annotations

import pytest

from backend.db.income_event_store import IncomeEventStore


@pytest.fixture
def store(db_isolated):
    return IncomeEventStore()


def _seed(store):
    store.append(
        {"instrument_id": "i1", "position_id": "p1", "paid_at": "2026-01-15", "gross_amount": 10}
    )
    store.append(
        {"instrument_id": "i2", "position_id": "p2", "paid_at": "2026-03-01", "gross_amount": 20}
    )
    store.append(
        {
            "instrument_id": "i1",
            "position_id": "p1",
            "paid_at": "2026-02-10",
            "gross_amount": 30,
            "idempotency_key": "k-1",
        }
    )


def test_append_stamps_id_and_created_at(store):
    ev = store.append({"gross_amount": 5})
    assert ev["id"]
    assert ev["created_at"]


def test_query_sorted_by_paid_at(store):
    _seed(store)
    out = store.query_events()
    assert [e["paid_at"] for e in out] == ["2026-01-15", "2026-02-10", "2026-03-01"]


def test_query_date_filter(store):
    _seed(store)
    out = store.query_events(date_from="2026-02-01", date_to="2026-02-28")
    assert [e["paid_at"] for e in out] == ["2026-02-10"]


def test_query_instrument_filter(store):
    _seed(store)
    out = store.query_events(instrument_id="i1")
    assert {e["instrument_id"] for e in out} == {"i1"}
    assert len(out) == 2


def test_find_by_idempotency_key(store):
    _seed(store)
    assert store.find_by_idempotency_key("k-1")["gross_amount"] == 30
    assert store.find_by_idempotency_key("missing") is None
    assert store.find_by_idempotency_key("") is None
