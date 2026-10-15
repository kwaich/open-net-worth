"""
CRUD/query tests for VestEventStore — pattern mirrors tests/test_income_store.py.
"""

from __future__ import annotations

import pytest

from backend.db.vest_event_store import VestEventStore


@pytest.fixture
def store(tmp_path, monkeypatch):
    return VestEventStore()


def _ev(grant_id="g1", tranche_id="t1", vest_date="2025-03-15", idempotency_key=None):
    row = {
        "grant_id": grant_id,
        "tranche_id": tranche_id,
        "date": vest_date,
        "units": 300.0,
        "fmv_per_unit": 30.0,
        "fmv_currency": "SGD",
    }
    if idempotency_key:
        row["idempotency_key"] = idempotency_key
    return row


def test_append_stamps_id_and_created_at(store):
    ev = store.append(_ev())
    assert ev["id"]
    assert ev["created_at"]


def test_query_events_sorted_by_date(store):
    store.append(_ev(vest_date="2025-06-15"))
    store.append(_ev(vest_date="2025-03-15"))
    store.append(_ev(vest_date="2025-09-15"))
    out = store.query_events()
    assert [e["date"] for e in out] == ["2025-03-15", "2025-06-15", "2025-09-15"]


def test_query_events_date_filter(store):
    store.append(_ev(vest_date="2025-03-15"))
    store.append(_ev(vest_date="2025-06-15"))
    out = store.query_events(date_from="2025-06-01", date_to="2025-12-31")
    assert len(out) == 1
    assert out[0]["date"] == "2025-06-15"


def test_query_events_grant_id_filter(store):
    store.append(_ev(grant_id="g1"))
    store.append(_ev(grant_id="g2"))
    out = store.query_events(grant_id="g1")
    assert all(e["grant_id"] == "g1" for e in out)
    assert len(out) == 1


def test_find_by_idempotency_key(store):
    store.append(_ev(idempotency_key="idem-1"))
    store.append(_ev(idempotency_key="idem-2"))
    found = store.find_by_idempotency_key("idem-1")
    assert found is not None
    assert found["idempotency_key"] == "idem-1"


def test_find_by_idempotency_key_missing_returns_none(store):
    assert store.find_by_idempotency_key("no-such-key") is None


def test_find_by_idempotency_key_empty_never_matches(store):
    store.append(_ev())
    assert store.find_by_idempotency_key("") is None


def test_find_by_grant_tranche(store):
    store.append(_ev(grant_id="g1", tranche_id="t1"))
    store.append(_ev(grant_id="g1", tranche_id="t2"))
    found = store.find_by_grant_tranche("g1", "t1")
    assert found is not None
    assert found["tranche_id"] == "t1"


def test_find_by_grant_tranche_not_found(store):
    store.append(_ev(grant_id="g1", tranche_id="t1"))
    assert store.find_by_grant_tranche("g1", "t-nonexistent") is None


def test_delete_event(store):
    ev = store.append(_ev())
    assert store.delete_event(ev["id"]) is True
    assert store.find_by_id(ev["id"]) is None


def test_delete_event_not_found(store):
    assert store.delete_event("nonexistent") is False
