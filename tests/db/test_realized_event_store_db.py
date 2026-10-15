"""DB-backed CRUD + money-exactness tests for RealizedEventStore (plan 4b, task 2)."""

from __future__ import annotations

from sqlalchemy import select

from backend.db.realized_event_store import RealizedEventStore
from backend.db.tables import DEFAULT_BOOK_ID, realized_events


def _event(**extra):
    return {
        "position_id": "p1",
        "instrument_id": "i1",
        "account_id": "a1",
        "disposed_at": "2026-02-01",
        "quantity": 5.0,
        "proceeds_per_unit": 20.0,
        "proceeds_currency": "USD",
        **extra,
    }


def test_append_and_find_round_trip(db_engine):
    s = RealizedEventStore(engine=db_engine)
    ev = s.append(_event())
    assert ev["id"] and ev["created_at"]
    assert s.find_by_id(ev["id"])["position_id"] == "p1"


def test_writes_default_book_id(db_engine):
    s = RealizedEventStore(engine=db_engine)
    ev = s.append(_event())
    with db_engine.connect() as conn:
        row = conn.execute(select(realized_events).where(realized_events.c.id == ev["id"])).first()
    assert row.book_id == DEFAULT_BOOK_ID


def test_query_events_sorted_and_filtered(db_engine):
    """query_events/find_by_idempotency_key were rewritten from self._load_items() to
    self.all() — proves the date/instrument/account/position filters and chronological
    sort are unchanged post-rewrite."""
    s = RealizedEventStore(engine=db_engine)
    s.append(_event(disposed_at="2026-03-01"))
    s.append(
        _event(disposed_at="2026-01-01", instrument_id="i2", account_id="a2", position_id="p2")
    )
    s.append(_event(disposed_at="2026-02-01"))

    out = s.query_events()
    assert [e["disposed_at"] for e in out] == ["2026-01-01", "2026-02-01", "2026-03-01"]
    assert len(s.query_events(instrument_id="i1")) == 2
    assert len(s.query_events(account_id="a2")) == 1
    assert len(s.query_events(position_id="p2")) == 1
    windowed = s.query_events(date_from="2026-02-01", date_to="2026-02-28")
    assert [e["disposed_at"] for e in windowed] == ["2026-02-01"]


def test_find_by_idempotency_key(db_engine):
    s = RealizedEventStore(engine=db_engine)
    s.append(_event(idempotency_key="k-1"))
    assert s.find_by_idempotency_key("k-1")["position_id"] == "p1"
    assert s.find_by_idempotency_key("missing") is None
    assert s.find_by_idempotency_key("") is None


def test_realized_event_consumptions_survive_verbatim(db_engine):
    """money in payload (proceeds/realized_gain/fx/consumptions) — exact via JSON
    round-trip, including nested consumption-line gains."""
    s = RealizedEventStore(engine=db_engine)
    ev = s.append(
        _event(
            realized_gain=90.5,
            realized_gain_base=90.5,
            fx_rate=0.1 + 0.2,
            consumptions=[{"lot_id": "l1", "qty": 2, "realized_gain": 45.25}],
        )
    )
    back = s.find_by_id(ev["id"])
    assert back["consumptions"][0]["realized_gain"] == 45.25
    assert back["realized_gain"] == 90.5
    assert back["fx_rate"] == 0.1 + 0.2
