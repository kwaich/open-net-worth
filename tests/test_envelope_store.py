"""
Unit tests for db.envelope_store.EnvelopeStore + the services.envelopes read functions that
sit directly on top of it (env_of / budgeted_category_ids).

Fully isolated: DB-backed (plan 4a task 2) via db_isolated, a fresh database per test.
services.envelopes.env_of/budgeted_category_ids read the module singleton
(envelopes_service.envelope_store), not the local `store` fixture — both resolve the SAME
db_isolated-redirected engine, so no per-instance path-sync is needed (mirrors
tests/test_category_store.py + tests/test_planning_service.py).
"""

from __future__ import annotations

import pytest

from backend.db.envelope_store import EnvelopeStore
from backend.services import envelopes as envelopes_service


@pytest.fixture
def store(db_isolated):
    return EnvelopeStore()


_GROCERIES = {
    "id": "env_groceries",
    "name": "Groceries",
    "category_ids": ["food"],
    "group_id": "grp_essentials",
    "order": 0,
    "archived": False,
}
_EATING_OUT = {
    "id": "env_eating_out",
    "name": "Eating Out",
    "category_ids": ["dining", "entertainment"],
    "group_id": "grp_essentials",
    "order": 1,
    "archived": False,
}
_ARCHIVED = {
    "id": "env_old",
    "name": "Old Hobby",
    "category_ids": ["hobby"],
    "group_id": None,
    "order": 2,
    "archived": True,
}


# ── CRUD ─────────────────────────────────────────────────────────────────────────


def test_empty_store_returns_empty_list(store):
    assert store.all() == []


def test_create_mints_id_and_defaults(store):
    env = store.create({"name": "Groceries", "category_ids": ["food"]})
    assert env["id"].startswith("env_")
    assert env["category_ids"] == ["food"]
    assert env["group_id"] is None
    assert env["archived"] is False
    assert env["color"] == ""
    assert env["archive_reason"] == ""
    assert env["merged_into"] == ""
    assert env["former_category_ids"] == []
    assert env["archived_at"] == ""
    # Money (assigned/target) and notes are stored elsewhere, envelope-id keyed
    # (docs/adr/2026-07-05-envelope-id-native.md) — never on the envelope row itself.
    assert "note" not in env
    assert "target" not in env


def test_create_carries_color_and_archive_metadata(store):
    env = store.create(
        {
            "name": "Groceries",
            "color": "#3b86dd",
            "archive_reason": "deleted",
            "merged_into": "env_target",
            "former_category_ids": ["food", "food", ""],
            "archived_at": "2026-06-01T00:00:00",
        }
    )
    assert env["color"] == "#3b86dd"
    assert env["archive_reason"] == "deleted"
    assert env["merged_into"] == "env_target"
    assert env["former_category_ids"] == ["food"]  # cleaned like category_ids
    assert env["archived_at"] == "2026-06-01T00:00:00"


def test_update_envelope_patches_archive_metadata_fields(store):
    store.insert(dict(_GROCERIES))
    updated = store.update_envelope(
        "env_groceries",
        {
            "color": "#00ff00",
            "archived": True,
            "archive_reason": "deleted",
            "merged_into": "env_target",
            "former_category_ids": ["food"],
            "archived_at": "2026-06-01T00:00:00",
        },
    )
    assert updated["color"] == "#00ff00"
    assert updated["archive_reason"] == "deleted"
    assert updated["merged_into"] == "env_target"
    assert updated["former_category_ids"] == ["food"]
    assert updated["archived_at"] == "2026-06-01T00:00:00"


def test_create_rejects_blank_name(store):
    with pytest.raises(ValueError):
        store.create({"name": "", "category_ids": ["food"]})


def test_create_assigns_incrementing_order(store):
    first = store.create({"name": "A"})
    second = store.create({"name": "B"})
    assert first["order"] == 0
    assert second["order"] == 1


def test_bulk_insert(store):
    store.bulk_insert([dict(_GROCERIES), dict(_EATING_OUT)])
    assert len(store.all()) == 2


def test_list_ordered_sorts_by_order_then_name(store):
    store.bulk_insert([dict(_EATING_OUT), dict(_GROCERIES)])
    ordered = store.list_ordered()
    assert [e["id"] for e in ordered] == ["env_groceries", "env_eating_out"]


def test_update_envelope_patches_whitelisted_fields(store):
    store.insert(dict(_GROCERIES))
    updated = store.update_envelope(
        "env_groceries", {"name": "Food", "color": "#3b86dd", "bogus": "nope"}
    )
    assert updated["name"] == "Food"
    assert updated["color"] == "#3b86dd"
    assert "bogus" not in updated
    # note/target aren't editable fields on this store — they live elsewhere.
    assert "note" not in updated
    assert "target" not in updated


def test_update_envelope_cleans_category_keys(store):
    store.insert(dict(_GROCERIES))
    updated = store.update_envelope(
        "env_groceries", {"category_ids": ["food", "food", "", "dining"]}
    )
    assert updated["category_ids"] == ["food", "dining"]


def test_update_envelope_missing_returns_none(store):
    assert store.update_envelope("ghost", {"name": "x"}) is None


def test_update_envelope_archives(store):
    store.insert(dict(_GROCERIES))
    updated = store.update_envelope("env_groceries", {"archived": True})
    assert updated["archived"] is True


def test_update_envelope_no_editable_changes_returns_current(store):
    store.insert(dict(_GROCERIES))
    updated = store.update_envelope("env_groceries", {"id": "should-be-ignored"})
    assert updated["id"] == "env_groceries"


# ── services.envelopes.env_of / budgeted_category_ids ────────────────────────────


def test_env_of_maps_each_category_to_its_envelope(store):
    store.bulk_insert([dict(_GROCERIES), dict(_EATING_OUT)])
    idx = envelopes_service.env_of()
    assert idx == {
        "food": "env_groceries",
        "dining": "env_eating_out",
        "entertainment": "env_eating_out",
    }


def test_env_of_excludes_archived_envelopes(store):
    store.bulk_insert([dict(_GROCERIES), dict(_ARCHIVED)])
    idx = envelopes_service.env_of()
    assert idx == {"food": "env_groceries"}
    assert "hobby" not in idx


def test_env_of_is_single_valued_per_category(store):
    # A category maps to AT MOST one envelope (a partition) — env_of is a dict, so even
    # malformed data (two envelopes claiming the same category) can never yield two entries
    # for one key. Real uniqueness is enforced at the write choke point (P2); this documents
    # the read side never fans a category out to more than one id.
    dup_a = {**dict(_GROCERIES), "id": "env_a", "order": 0}
    dup_b = {**dict(_GROCERIES), "id": "env_b", "order": 1}
    store.bulk_insert([dup_a, dup_b])
    idx = envelopes_service.env_of()
    assert len(idx) == 1
    assert idx["food"] in {"env_a", "env_b"}


def test_budgeted_category_keys_matches_env_of_keys(store):
    store.bulk_insert([dict(_GROCERIES), dict(_EATING_OUT), dict(_ARCHIVED)])
    assert envelopes_service.budgeted_category_ids() == {"food", "dining", "entertainment"}


def test_budgeted_category_keys_empty_when_no_envelopes(store):
    assert envelopes_service.budgeted_category_ids() == set()


def test_all_envelopes_includes_archived(store):
    store.bulk_insert([dict(_GROCERIES), dict(_ARCHIVED)])
    ids = {e["id"] for e in envelopes_service.all_envelopes()}
    assert ids == {"env_groceries", "env_old"}
