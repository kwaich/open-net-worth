"""DB-backed CRUD tests for GrantStore (plan 4b, task 2).

No money column on grants (units are counts; value is derived) — total_units and a
tranche's units stand in for the money-exactness property (both are payload floats
that must round-trip exactly through JSON, same as a money field would)."""

from __future__ import annotations

from sqlalchemy import select

from backend.db.grant_store import GrantStore
from backend.db.tables import DEFAULT_BOOK_ID, grants


def _grant(instrument_id="i1", grant_date="2024-03-15", status="active", **extra):
    return {
        "instrument_id": instrument_id,
        "grant_date": grant_date,
        "total_units": 1200.0,
        "status": status,
        "tax_event": "at_vest",
        "tranches": [],
        **extra,
    }


def test_append_and_find_round_trip(db_engine):
    s = GrantStore(engine=db_engine)
    g = s.append(_grant())
    assert g["id"] and g["created_at"] and g["updated_at"]
    assert s.find_by_id(g["id"])["instrument_id"] == "i1"


def test_writes_default_book_id(db_engine):
    s = GrantStore(engine=db_engine)
    g = s.append(_grant())
    with db_engine.connect() as conn:
        row = conn.execute(select(grants).where(grants.c.id == g["id"])).first()
    assert row.book_id == DEFAULT_BOOK_ID


def test_query_grants_sorted_and_filtered(db_engine):
    """query_grants/find_by_idempotency_key were rewritten from self._load_items() to
    self.all() — proves sort-by-grant_date and the status/instrument_id filters hold."""
    s = GrantStore(engine=db_engine)
    s.append(_grant("i1", "2024-01-01", status="active"))
    s.append(_grant("i2", "2023-06-01", status="fully_vested"))
    s.append(_grant("i1", "2024-05-01", status="cancelled"))

    out = s.query_grants()
    assert [g["grant_date"] for g in out] == ["2023-06-01", "2024-01-01", "2024-05-01"]
    assert len(s.query_grants(status="active")) == 1
    assert len(s.query_grants(instrument_id="i1")) == 2


def test_update_grant_patches_and_persists(db_engine):
    s = GrantStore(engine=db_engine)
    g = s.append(_grant())
    updated = s.update_grant(g["id"], {"status": "fully_vested"})
    assert updated["status"] == "fully_vested"
    assert updated["updated_at"] >= g["created_at"]
    assert s.find_by_id(g["id"])["status"] == "fully_vested"
    assert s.update_grant("nonexistent", {"status": "x"}) is None


def test_find_by_idempotency_key(db_engine):
    s = GrantStore(engine=db_engine)
    s.append(_grant(idempotency_key="k-1"))
    assert s.find_by_idempotency_key("k-1")["instrument_id"] == "i1"
    assert s.find_by_idempotency_key("missing") is None
    assert s.find_by_idempotency_key("") is None


def test_total_units_and_tranche_units_round_trip_exactly(db_engine):
    s = GrantStore(engine=db_engine)
    tricky = 0.1 + 0.2
    g = s.append(_grant(total_units=tricky, tranches=[{"id": "t1", "units": tricky}]))
    back = s.find_by_id(g["id"])
    assert back["total_units"] == tricky
    assert back["tranches"][0]["units"] == tricky
