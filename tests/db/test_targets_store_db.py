"""DB-backed CRUD + money-exactness tests for TargetsStore (plan 4a, task 3)."""

from __future__ import annotations

from sqlalchemy import select

from backend.db.tables import DEFAULT_BOOK_ID, targets
from backend.db.targets_store import TargetsStore


def test_create_find_and_delete_round_trip(db_engine):
    s = TargetsStore(engine=db_engine)
    t = s.create({"name": "Emergency fund", "target_amount": 12000, "target_date": "2026-12-31"})
    assert t["id"].startswith("tgt_")
    assert s.find_by_id(t["id"])["name"] == "Emergency fund"
    assert s.delete(t["id"]) is True
    assert s.find_by_id(t["id"]) is None


def test_writes_default_book_id(db_engine):
    s = TargetsStore(engine=db_engine)
    t = s.create({"name": "Trip", "target_amount": 500, "target_date": "2026-09-30"})
    with db_engine.connect() as conn:
        row = conn.execute(select(targets).where(targets.c.id == t["id"])).first()
    assert row.book_id == DEFAULT_BOOK_ID


def test_target_amount_round_trips_exactly(db_engine):
    s = TargetsStore(engine=db_engine)
    t = s.create(
        {
            "name": "House",
            "target_amount": 0.1 + 0.2,
            "currency": "USD",
            "target_date": "2030-01-01",
        }
    )
    got = s.find_by_id(t["id"])["target_amount"]
    assert got == 0.1 + 0.2  # payload float, exact (no Float-column coercion)


def test_query_targets_filters_by_status(db_engine):
    """query_targets was rewritten from self._load_items() to self.all() — this proves
    the filter behavior is unchanged post-rewrite."""
    s = TargetsStore(engine=db_engine)
    a = s.create({"name": "A", "target_amount": 10, "target_date": "2026-12-31"})
    b = s.create({"name": "B", "target_amount": 10, "target_date": "2026-12-31"})
    s.update_target(b["id"], {"status": "completed"})
    assert {t["id"] for t in s.query_targets(status="active")} == {a["id"]}
    assert {t["id"] for t in s.query_targets(status="completed")} == {b["id"]}
    assert len(s.query_targets()) == 2


def test_update_target_whitelist_and_coercion(db_engine):
    s = TargetsStore(engine=db_engine)
    t = s.create({"name": "EF", "target_amount": 100, "target_date": "2026-12-31"})
    updated = s.update_target(t["id"], {"id": "hacked", "target_amount": "200"})
    assert updated["id"] == t["id"]  # id immutable
    assert updated["target_amount"] == 200.0  # coerced from string
