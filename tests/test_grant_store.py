"""
CRUD/query tests for GrantStore — pattern mirrors tests/test_income_store.py.
"""

from __future__ import annotations

import pytest

from backend.db.grant_store import GrantStore


@pytest.fixture
def store(tmp_path, monkeypatch):
    return GrantStore()


def _grant(instrument_id="i1", grant_date="2024-03-15", status="active", tax_event="at_vest"):
    return {
        "instrument_id": instrument_id,
        "grant_date": grant_date,
        "total_units": 1200.0,
        "status": status,
        "tax_event": tax_event,
        "tranches": [],
    }


def test_append_stamps_id_and_timestamps(store):
    g = store.append(_grant())
    assert g["id"]
    assert g["created_at"]
    assert g["updated_at"]


def test_query_grants_all(store):
    store.append(_grant("i1", "2024-01-01"))
    store.append(_grant("i2", "2023-06-01"))
    out = store.query_grants()
    # sorted by grant_date
    assert [g["grant_date"] for g in out] == ["2023-06-01", "2024-01-01"]


def test_query_grants_status_filter(store):
    store.append(_grant(status="active"))
    store.append(_grant(status="fully_vested"))
    store.append(_grant(status="cancelled"))
    active = store.query_grants(status="active")
    assert len(active) == 1
    assert active[0]["status"] == "active"


def test_query_grants_instrument_filter(store):
    store.append(_grant("i1"))
    store.append(_grant("i2"))
    out = store.query_grants(instrument_id="i1")
    assert len(out) == 1
    assert out[0]["instrument_id"] == "i1"


def test_update_grant_patches_fields(store):
    g = store.append(_grant())
    updated = store.update_grant(g["id"], {"status": "fully_vested"})
    assert updated["status"] == "fully_vested"
    assert updated["updated_at"] >= g["created_at"]
    # Verify persisted
    found = store.find_by_id(g["id"])
    assert found["status"] == "fully_vested"


def test_update_grant_not_found_returns_none(store):
    result = store.update_grant("nonexistent-id", {"status": "cancelled"})
    assert result is None


def test_find_by_id_after_append(store):
    g = store.append(_grant("i1"))
    found = store.find_by_id(g["id"])
    assert found["instrument_id"] == "i1"


def test_delete_grant(store):
    g = store.append(_grant())
    store.delete(g["id"])
    assert store.find_by_id(g["id"]) is None
