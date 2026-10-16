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


def test_a_retired_declared_row_is_not_an_overlay(db_engine):
    """Declared rows (retired 2026-10-16) stay in the table untouched; overlays() skips them."""
    s = RecurringStreamStore(engine=db_engine)
    s.insert({"type": "declared", "id": "rst_old", "amount": 5400.0, "label": "Salary"})
    s.set_status("sig-1", "confirmed")
    assert list(s.overlays()) == ["sig-1"]
    assert s.find_by_id("rst_old")["amount"] == 5400.0
