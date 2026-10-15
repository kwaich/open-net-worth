"""DB-backed CRUD + money-exactness tests for VestEventStore (plan 4b, task 2)."""

from __future__ import annotations

from sqlalchemy import select

from backend.db.tables import DEFAULT_BOOK_ID, vest_events
from backend.db.vest_event_store import VestEventStore


def _ev(grant_id="g1", tranche_id="t1", vest_date="2025-03-15", **extra):
    return {
        "grant_id": grant_id,
        "tranche_id": tranche_id,
        "date": vest_date,
        "units": 300.0,
        "fmv_per_unit": 30.0,
        "fmv_currency": "SGD",
        **extra,
    }


def test_append_and_find_round_trip(db_engine):
    s = VestEventStore(engine=db_engine)
    ev = s.append(_ev())
    assert ev["id"] and ev["created_at"]
    assert s.find_by_id(ev["id"])["grant_id"] == "g1"


def test_writes_default_book_id(db_engine):
    s = VestEventStore(engine=db_engine)
    ev = s.append(_ev())
    with db_engine.connect() as conn:
        row = conn.execute(select(vest_events).where(vest_events.c.id == ev["id"])).first()
    assert row.book_id == DEFAULT_BOOK_ID


def test_query_events_sorted_and_filtered(db_engine):
    """query_events/find_by_idempotency_key/find_by_grant_tranche/query_pending_releases
    were rewritten from self._load_items() to self.all() — proves behavior is unchanged."""
    s = VestEventStore(engine=db_engine)
    s.append(_ev(vest_date="2025-06-15"))
    s.append(_ev(vest_date="2025-03-15"))
    s.append(_ev(grant_id="g2", vest_date="2025-09-15"))

    out = s.query_events()
    assert [e["date"] for e in out] == ["2025-03-15", "2025-06-15", "2025-09-15"]
    assert len(s.query_events(grant_id="g1")) == 2
    assert len(s.query_events(date_from="2025-06-01", date_to="2025-12-31")) == 2


def test_find_by_idempotency_key_and_grant_tranche(db_engine):
    s = VestEventStore(engine=db_engine)
    s.append(_ev(tranche_id="t1", idempotency_key="k-1"))
    s.append(_ev(tranche_id="t2"))
    assert s.find_by_idempotency_key("k-1")["tranche_id"] == "t1"
    assert s.find_by_idempotency_key("") is None
    assert s.find_by_grant_tranche("g1", "t2")["tranche_id"] == "t2"
    assert s.find_by_grant_tranche("g1", "no-such-tranche") is None


def test_query_pending_releases_and_update_and_delete(db_engine):
    s = VestEventStore(engine=db_engine)
    pending = s.append(
        _ev(tranche_id="t1", pending_release=True, expected_release_date="2025-04-01")
    )
    s.append(_ev(tranche_id="t2", pending_release=False))

    out = s.query_pending_releases()
    assert [e["id"] for e in out] == [pending["id"]]

    updated = s.update_event(pending["id"], {"pending_release": False, "released_on": "2025-04-02"})
    assert updated["released_on"] == "2025-04-02"
    assert s.query_pending_releases() == []

    assert s.delete_event(pending["id"]) is True
    assert s.find_by_id(pending["id"]) is None
    assert s.delete_event("nonexistent") is False


def test_fmv_gross_gain_and_base_amount_round_trip_exactly(db_engine):
    """money in payload (fmv/gross_gain/base_amount) — exact via JSON round-trip."""
    s = VestEventStore(engine=db_engine)
    tricky = 0.1 + 0.2
    ev = s.append(
        _ev(fmv_per_unit=tricky, gross_gain_native=tricky, base_amount=tricky, fx_rate=tricky)
    )
    back = s.find_by_id(ev["id"])
    assert back["fmv_per_unit"] == tricky
    assert back["gross_gain_native"] == tricky
    assert back["base_amount"] == tricky
    assert back["fx_rate"] == tricky
