"""EnvelopeGroupsStore — DB-backed (plan 4a task 2), on BookPayloadTableStore. No
promoted columns beyond book_id/seq/id/payload, so every domain method is verbatim
from the JsonStore era; only construction (``engine=``) and the base class changed.
"""

from __future__ import annotations

import pytest

from backend.db.envelope_groups_store import EnvelopeGroupsStore


def test_create_then_list_ordered_round_trip(db_engine):
    store = EnvelopeGroupsStore(engine=db_engine)
    store.create({"name": "Zeta", "categories": ["z"]})  # a stray membership list is dropped
    store.create({"name": "Alpha"})
    ordered = store.list_ordered()
    # order is assigned at create time (len(existing)) — insertion order, not name order.
    assert [g["name"] for g in ordered] == ["Zeta", "Alpha"]
    assert "categories" not in store.find_by_id(ordered[0]["id"])


def test_writes_default_book_id(db_engine):
    from sqlalchemy import select

    from backend.db.tables import DEFAULT_BOOK_ID, envelope_groups

    store = EnvelopeGroupsStore(engine=db_engine)
    created = store.create({"name": "Essentials"})
    with db_engine.connect() as conn:
        row = conn.execute(
            select(envelope_groups).where(envelope_groups.c.id == created["id"])
        ).first()
    assert row.book_id == DEFAULT_BOOK_ID


def test_update_group_patches_whitelisted_fields_only(db_engine):
    store = EnvelopeGroupsStore(engine=db_engine)
    created = store.create({"name": "Essentials"})
    updated = store.update_group(created["id"], {"name": "Core", "is_others": True})
    assert updated["name"] == "Core"
    assert updated["is_others"] is False  # not in _EDITABLE — internal-only flag


def test_update_group_missing_returns_none(db_engine):
    store = EnvelopeGroupsStore(engine=db_engine)
    assert store.update_group("ghost", {"name": "x"}) is None


def test_ensure_others_idempotent(db_engine):
    store = EnvelopeGroupsStore(engine=db_engine)
    first = store.ensure_others()
    second = store.ensure_others()
    assert first["id"] == second["id"]
    assert len([g for g in store.all() if g.get("is_others")]) == 1


def test_reorder_restamps_order_and_persists(db_engine):
    store = EnvelopeGroupsStore(engine=db_engine)
    a = store.create({"name": "A"})
    b = store.create({"name": "B"})
    reordered = store.reorder([b["id"], a["id"]])
    assert [g["id"] for g in reordered] == [b["id"], a["id"]]
    assert store.find_by_id(b["id"])["order"] == 0
    assert store.find_by_id(a["id"])["order"] == 1


def test_create_rejects_blank_name(db_engine):
    store = EnvelopeGroupsStore(engine=db_engine)
    with pytest.raises(ValueError):
        store.create({"name": ""})
