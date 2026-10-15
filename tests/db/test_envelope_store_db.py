"""EnvelopeStore — DB-backed (plan 4a task 2), on BookPayloadTableStore. NO money —
money is envelope-id-native in budget_assignments/profile (ADR 2026-07-05); this
store carries only the envelope shell (name/category_ids/group_id/order/archive*).
"""

from __future__ import annotations

import pytest

from backend.db.envelope_store import EnvelopeStore


def test_create_then_find_round_trip(db_engine):
    store = EnvelopeStore(engine=db_engine)
    env = store.create({"name": "Groceries", "category_ids": ["food", "food"]})
    assert env["id"].startswith("env_")
    assert env["category_ids"] == ["food"]  # deduped
    assert store.find_by_id(env["id"])["name"] == "Groceries"


def test_list_ordered_sorts_by_order_then_name(db_engine):
    store = EnvelopeStore(engine=db_engine)
    store.create({"name": "B"})
    store.create({"name": "A"})
    assert [e["name"] for e in store.list_ordered()] == ["B", "A"]  # insertion order (0, 1)


def test_update_envelope_patches_whitelisted_and_archives(db_engine):
    store = EnvelopeStore(engine=db_engine)
    env = store.create({"name": "Groceries"})
    updated = store.update_envelope(env["id"], {"archived": True, "archive_reason": "merged"})
    assert updated["archived"] is True
    assert updated["archive_reason"] == "merged"
    assert store.find_by_id(env["id"])["archived"] is True


def test_update_envelope_missing_returns_none(db_engine):
    store = EnvelopeStore(engine=db_engine)
    assert store.update_envelope("ghost", {"name": "x"}) is None


def test_create_rejects_blank_name(db_engine):
    store = EnvelopeStore(engine=db_engine)
    with pytest.raises(ValueError):
        store.create({"name": ""})
