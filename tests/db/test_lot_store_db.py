"""DB-backed CRUD + atomicity tests for LotStore (plan 4b, task 2).

bulk_consume/bulk_restore are the ONE pair of custom multi-row writers among the six
Tier-1 investment stores — rewritten onto the base ``_use_conn`` transaction seam with
an additive ``conn=`` (Task 4's trade-atomicity choke threads it so a sell's
lot-consumption + position update + realized-event write commit in ONE transaction).
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from backend.db.engine import locked_unit_of_work
from backend.db.lot_store import LotStore
from backend.db.tables import DEFAULT_BOOK_ID, lots


def test_create_and_find_round_trip(db_engine):
    s = LotStore(engine=db_engine)
    lot = s.create(
        {"position_id": "p1", "original_qty": 10, "unit_cost": 5.0, "cost_currency": "usd"}
    )
    assert lot["id"] and lot["created_at"] and lot["updated_at"]
    assert lot["remaining_qty"] == 10.0
    assert lot["cost_currency"] == "USD"
    found = s.find_by_id(lot["id"])
    assert found["original_qty"] == 10.0


def test_writes_default_book_id(db_engine):
    s = LotStore(engine=db_engine)
    lot = s.create({"position_id": "p1", "original_qty": 1})
    with db_engine.connect() as conn:
        row = conn.execute(select(lots).where(lots.c.id == lot["id"])).first()
    assert row.book_id == DEFAULT_BOOK_ID


def test_lots_for_position_fifo_sorted_and_filters_closed(db_engine):
    """lots_for_position/open_lots_for_position were rewritten from self._load_items() to
    self.all() — proves the FIFO ordering and include_closed filter are unchanged."""
    s = LotStore(engine=db_engine)
    late = s.create({"position_id": "p1", "acquired_at": "2026-03-01", "original_qty": 1})
    early = s.create({"position_id": "p1", "acquired_at": "2026-01-01", "original_qty": 1})
    closed = s.create(
        {
            "position_id": "p1",
            "acquired_at": "2026-02-01",
            "original_qty": 1,
            "remaining_qty": 0,
        }
    )
    other = s.create({"position_id": "p2", "acquired_at": "2026-01-01", "original_qty": 1})

    ordered = s.lots_for_position("p1")
    assert [lot["id"] for lot in ordered] == [early["id"], closed["id"], late["id"]]

    open_only = s.open_lots_for_position("p1")
    assert [lot["id"] for lot in open_only] == [early["id"], late["id"]]
    assert other["id"] not in {lot["id"] for lot in ordered}


def test_find_by_idempotency_key(db_engine):
    s = LotStore(engine=db_engine)
    s.create({"position_id": "p1", "original_qty": 1, "idempotency_key": "k-1"})
    assert s.find_by_idempotency_key("k-1")["position_id"] == "p1"
    assert s.find_by_idempotency_key("missing") is None
    assert s.find_by_idempotency_key("") is None


def test_unit_cost_and_fees_round_trip_exactly(db_engine):
    """unit_cost/fees/fx_rate live in payload — exact via JSON round-trip, no promoted
    decimal column."""
    s = LotStore(engine=db_engine)
    tricky = 0.1 + 0.2
    lot = s.create(
        {
            "position_id": "p1",
            "original_qty": 3,
            "unit_cost": tricky,
            "fees": tricky,
            "cost_currency": "USD",
        }
    )
    back = s.find_by_id(lot["id"])
    assert back["unit_cost"] == tricky
    assert back["fees"] == tricky


def test_bulk_consume_atomic_and_clamps_dust(db_engine):
    s = LotStore(engine=db_engine)
    a = s.create({"position_id": "p1", "original_qty": 10, "remaining_qty": 10})
    b = s.create({"position_id": "p1", "original_qty": 5, "remaining_qty": 5})
    touched = s.bulk_consume([(a["id"], 10.0), (b["id"], 2.0)])
    assert {t["id"] for t in touched} == {a["id"], b["id"]}
    assert s.find_by_id(a["id"])["remaining_qty"] == 0.0  # exact close, no 1e-12 dust
    assert s.find_by_id(b["id"])["remaining_qty"] == 3.0


def test_bulk_consume_sums_duplicate_lot_ids(db_engine):
    s = LotStore(engine=db_engine)
    a = s.create({"position_id": "p1", "original_qty": 10, "remaining_qty": 10})
    s.bulk_consume([(a["id"], 3.0), (a["id"], 4.0)])
    assert s.find_by_id(a["id"])["remaining_qty"] == 3.0


def test_bulk_consume_never_goes_negative(db_engine):
    s = LotStore(engine=db_engine)
    a = s.create({"position_id": "p1", "original_qty": 10, "remaining_qty": 10})
    s.bulk_consume([(a["id"], 999.0)])
    assert s.find_by_id(a["id"])["remaining_qty"] == 0.0


def test_bulk_restore_caps_at_original_qty(db_engine):
    s = LotStore(engine=db_engine)
    a = s.create({"position_id": "p1", "original_qty": 10, "remaining_qty": 4})
    touched = s.bulk_restore([(a["id"], 3.0)])
    assert touched[0]["remaining_qty"] == 7.0
    s.bulk_restore([(a["id"], 999.0)])
    assert s.find_by_id(a["id"])["remaining_qty"] == 10.0  # capped, never exceeds original


def test_bulk_consume_and_restore_accept_conn_and_compose_atomically(db_engine):
    """conn= threads the caller's OWN transaction (Task 4's trade-atomicity choke seam) —
    a mid-sequence failure after bulk_consume(conn=...) rolls the consumption back too,
    proving bulk_consume never opens its own transaction underneath the caller's."""
    s = LotStore(engine=db_engine)
    a = s.create({"position_id": "p1", "original_qty": 10, "remaining_qty": 10})

    with pytest.raises(RuntimeError, match="boom"):
        with locked_unit_of_work(lots) as conn:
            s.bulk_consume([(a["id"], 4.0)], conn=conn)
            raise RuntimeError("boom")
    assert s.find_by_id(a["id"])["remaining_qty"] == 10.0  # rolled back, unconsumed

    with locked_unit_of_work(lots) as conn:
        s.bulk_consume([(a["id"], 4.0)], conn=conn)
        s.bulk_restore([(a["id"], 1.0)], conn=conn)
    assert s.find_by_id(a["id"])["remaining_qty"] == 7.0  # both writes committed together
