"""DB-backed CRUD + money-exactness tests for RecurringStreamStore (plan 4a, task 3)."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from backend.db.recurring_stream_store import RecurringStreamStore
from backend.db.tables import DEFAULT_BOOK_ID, recurring_streams


def test_constructor_no_longer_accepts_path():
    """path= was the JsonStore-era constructor arg. Every real caller already constructs
    with no arguments (backend/api/recurring.py, backend/ai/tools/recurring.py,
    backend/services/recurring_baseline.py — grep-verified, see task-3 report); dropping
    it is safe and this locks that in as a regression test."""
    with pytest.raises(TypeError):
        RecurringStreamStore(path="/tmp/whatever.json")


def test_writes_default_book_id(db_engine):
    s = RecurringStreamStore(engine=db_engine)
    s.set_status("sig-1", "confirmed")
    with db_engine.connect() as conn:
        row = conn.execute(
            select(recurring_streams).where(recurring_streams.c.id == "sig-1")
        ).first()
    assert row.book_id == DEFAULT_BOOK_ID


def test_overlay_set_status_and_reset_round_trip(db_engine):
    s = RecurringStreamStore(engine=db_engine)
    s.set_status("sig-1", "dismissed", note="not mine")
    assert s.overlays()["sig-1"]["status"] == "dismissed"
    assert s.overlays()["sig-1"]["note"] == "not mine"
    assert s.reset("sig-1") is True
    assert s.overlays() == {}
    assert s.reset("sig-1") is False  # already gone


def test_declared_create_update_delete_round_trip(db_engine):
    s = RecurringStreamStore(engine=db_engine)
    row = s.create_declared(
        {
            "label": "Raise",
            "direction": "in",
            "amount": 19000,
            "cadence": "monthly",
            "anchor_date": "2026-09-01",
        }
    )
    assert row["id"].startswith("rst_")
    assert s.declared()[0]["id"] == row["id"]

    updated = s.update_declared(row["id"], {"amount": 20000})
    assert updated["amount"] == 20000.0

    assert s.delete_declared(row["id"]) is True
    assert s.declared() == []


def test_money_exactness_declared_amount(db_engine):
    """The declared amount lives inside the verbatim payload JSON — the round-trip
    through JSON's shortest-repr float encoding is exact."""
    s = RecurringStreamStore(engine=db_engine)
    row = s.create_declared(
        {
            "label": "Side income",
            "direction": "in",
            "amount": 19432.71,
            "cadence": "monthly",
            "anchor_date": "2026-09-01",
        }
    )
    assert s.find_by_id(row["id"])["amount"] == 19432.71
