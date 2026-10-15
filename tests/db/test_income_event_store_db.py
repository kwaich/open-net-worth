"""DB-backed CRUD + money-exactness tests for IncomeEventStore (plan 4a, task 3)."""

from __future__ import annotations

from sqlalchemy import select

from backend.db.income_event_store import IncomeEventStore
from backend.db.tables import DEFAULT_BOOK_ID, income_events


def test_append_and_find_round_trip(db_engine):
    s = IncomeEventStore(engine=db_engine)
    ev = s.append(
        {"instrument_id": "i1", "position_id": "p1", "paid_at": "2026-01-15", "gross_amount": 10}
    )
    assert ev["id"] and ev["created_at"]
    assert s.find_by_id(ev["id"])["gross_amount"] == 10


def test_writes_default_book_id(db_engine):
    s = IncomeEventStore(engine=db_engine)
    ev = s.append({"gross_amount": 5})
    with db_engine.connect() as conn:
        row = conn.execute(select(income_events).where(income_events.c.id == ev["id"])).first()
    assert row.book_id == DEFAULT_BOOK_ID


def test_money_exactness_gross_tax_base_and_fx(db_engine):
    """gross_amount/tax_withheld/base_amount/fx_rate all live inside the verbatim payload
    — the round-trip through JSON's shortest-repr float encoding is exact."""
    s = IncomeEventStore(engine=db_engine)
    tricky = 0.1 + 0.2
    ev = s.append(
        {
            "gross_amount": tricky,
            "tax_withheld": tricky,
            "base_amount": tricky,
            "fx_rate": tricky,
        }
    )
    back = s.find_by_id(ev["id"])
    assert back["gross_amount"] == tricky
    assert back["tax_withheld"] == tricky
    assert back["base_amount"] == tricky
    assert back["fx_rate"] == tricky


def test_find_by_idempotency_key(db_engine):
    s = IncomeEventStore(engine=db_engine)
    s.append({"gross_amount": 30, "idempotency_key": "k-1"})
    assert s.find_by_idempotency_key("k-1")["gross_amount"] == 30
    assert s.find_by_idempotency_key("missing") is None
    assert s.find_by_idempotency_key("") is None


def test_query_events_filters_and_sorts(db_engine):
    """find_by_idempotency_key/query_events were rewritten from self._load_items() to
    self.all() — this proves the filter/sort behavior is unchanged post-rewrite."""
    s = IncomeEventStore(engine=db_engine)
    s.append(
        {"instrument_id": "i1", "position_id": "p1", "paid_at": "2026-01-15", "gross_amount": 10}
    )
    s.append(
        {"instrument_id": "i2", "position_id": "p2", "paid_at": "2026-03-01", "gross_amount": 20}
    )
    s.append(
        {"instrument_id": "i1", "position_id": "p1", "paid_at": "2026-02-10", "gross_amount": 30}
    )

    out = s.query_events()
    assert [e["paid_at"] for e in out] == ["2026-01-15", "2026-02-10", "2026-03-01"]

    by_instrument = s.query_events(instrument_id="i1")
    assert {e["instrument_id"] for e in by_instrument} == {"i1"}
    assert len(by_instrument) == 2

    by_date = s.query_events(date_from="2026-02-01", date_to="2026-02-28")
    assert [e["paid_at"] for e in by_date] == ["2026-02-10"]
