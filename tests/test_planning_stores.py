"""
Unit tests for BudgetAssignmentsStore + EnvelopeGroupsStore (PLAN).

Pure, offline, fully isolated: both BudgetAssignmentsStore (plan 4a task 4) and
EnvelopeGroupsStore (plan 4a task 2) are DB-backed — each isolated via db_isolated, a fresh
empty database per test. Dumb-storage contract only (the math lives in services.planning).
"""

from __future__ import annotations

import pytest

from backend.db.budget_assignments_store import BudgetAssignmentsStore
from backend.db.envelope_groups_store import EnvelopeGroupsStore


@pytest.fixture
def assign_store(db_isolated):
    return BudgetAssignmentsStore()


@pytest.fixture
def groups_store(db_isolated):
    return EnvelopeGroupsStore()


# ── BudgetAssignmentsStore ───────────────────────────────────────────────────────


def test_assignments_empty_defaults(assign_store):
    assert assign_store.month_map("2026-06") == {}
    assert assign_store.assigned("2026-06", "food") == 0.0
    assert assign_store.months() == []


def test_assignments_set_and_read(assign_store):
    assign_store.set_assigned("2026-06", "food", 800)
    assign_store.set_assigned("2026-06", "transport", 200.5)
    assert assign_store.assigned("2026-06", "food") == 800.0
    assert assign_store.month_map("2026-06") == {"food": 800.0, "transport": 200.5}
    assert assign_store.months() == ["2026-06"]


def test_assignments_all_map_returns_every_month(assign_store):
    # all_map() folds the whole {month: {cat: amt}} in one load — drives cumulative
    # contributions for a "set aside by [date]" goal.
    assign_store.set_assigned("2026-05", "food", 100)
    assign_store.set_assigned("2026-06", "food", 200)
    assign_store.set_assigned("2026-06", "transport", 50)
    m = assign_store.all_map()
    assert m == {"2026-05": {"food": 100.0}, "2026-06": {"food": 200.0, "transport": 50.0}}
    # a returned inner dict is a copy — mutating it must not corrupt the store
    m["2026-06"]["food"] = 999
    assert assign_store.assigned("2026-06", "food") == 200.0


def test_assignments_zero_removes_entry(assign_store):
    assign_store.set_assigned("2026-06", "food", 800)
    assign_store.set_assigned("2026-06", "food", 0)
    assert assign_store.month_map("2026-06") == {}
    assert assign_store.months() == []  # empty month is pruned


def test_assignments_negative_is_stored_not_popped(assign_store):
    """FEB-02 fix (2026-07-04): only an EXACT zero pops the entry — a negative amount (the
    result of moving carried-over money out of an envelope past this month's own assigned)
    is stored as-is, not silently coerced to a pop like the old amt<=0 check did."""
    assign_store.set_assigned("2026-06", "food", 800)
    assign_store.set_assigned("2026-06", "food", -500)
    assert assign_store.assigned("2026-06", "food") == -500.0
    assert assign_store.month_map("2026-06") == {"food": -500.0}


def test_assignments_rounds(assign_store):
    assign_store.set_assigned("2026-06", "food", 12.349)
    assert assign_store.assigned("2026-06", "food") == 12.35


def test_assignments_persist_across_instances(assign_store):
    assign_store.set_assigned("2026-07", "food", 500)
    fresh = BudgetAssignmentsStore()
    assert fresh.assigned("2026-07", "food") == 500.0


# ── EnvelopeGroupsStore ──────────────────────────────────────────────────────────


def test_group_create_defaults(groups_store):
    g = groups_store.create({"name": "Essentials", "categories": ["food"]})
    assert g["id"].startswith("grp_")
    assert g["name"] == "Essentials"
    assert "categories" not in g  # a group holds envelopes, never categories
    assert g["collapsed"] is False
    assert g["order"] == 0


def test_group_create_requires_name(groups_store):
    with pytest.raises(ValueError):
        groups_store.create({"name": "  "})


def test_group_update_whitelist(groups_store):
    g = groups_store.create({"name": "A"})
    updated = groups_store.update_group(g["id"], {"name": "B", "collapsed": True, "bogus": 1})
    assert updated["name"] == "B" and updated["collapsed"] is True
    assert "bogus" not in updated


def test_group_update_missing_returns_none(groups_store):
    assert groups_store.update_group("grp-nope", {"name": "X"}) is None


def test_group_list_ordered(groups_store):
    a = groups_store.create({"name": "A"})
    b = groups_store.create({"name": "B"})
    groups_store.update_group(a["id"], {"order": 5})
    groups_store.update_group(b["id"], {"order": 1})
    names = [g["name"] for g in groups_store.list_ordered()]
    assert names == ["B", "A"]


def test_group_reorder(groups_store):
    a = groups_store.create({"name": "A"})
    b = groups_store.create({"name": "B"})
    c = groups_store.create({"name": "C"})
    groups_store.reorder([c["id"], a["id"], b["id"]])
    assert [g["name"] for g in groups_store.list_ordered()] == ["C", "A", "B"]


# ── ensure_others (the real default catch-all) ─────────────────────────────────────


def test_ensure_others_creates_once(groups_store):
    from backend.db.envelope_groups_store import OTHERS_GROUP_NAME

    g = groups_store.ensure_others()
    assert g["is_others"] is True
    assert g["name"] == OTHERS_GROUP_NAME
    assert "categories" not in g
    # a fresh mint gets an opaque id like any other group — no fixed sentinel.
    assert g["id"].startswith("grp")
    # idempotent — second call returns the same group, never a duplicate.
    again = groups_store.ensure_others()
    assert again["id"] == g["id"]
    assert len([x for x in groups_store.all() if x.get("is_others")]) == 1


def test_ensure_others_sorts_last(groups_store):
    groups_store.create({"name": "A"})
    groups_store.create({"name": "B"})
    others = groups_store.ensure_others()
    assert groups_store.list_ordered()[-1]["id"] == others["id"]


def test_ensure_others_ignores_a_stray_group_with_the_old_literal_id(groups_store):
    """No id is special-cased: a group that happens to carry the old literal id "others" but
    lacks the ``is_others`` flag is just an ordinary group — ensure_others() mints a genuinely
    new one rather than adopting it."""
    groups_store.insert({"id": "others", "name": "Holding", "collapsed": False, "order": 5})
    g = groups_store.ensure_others()
    assert g["id"] != "others"
    assert g["is_others"] is True
    ids_flagged = [x["id"] for x in groups_store.all() if x.get("is_others")]
    assert ids_flagged == [g["id"]]
