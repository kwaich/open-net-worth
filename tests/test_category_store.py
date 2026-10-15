"""
Unit tests for db.category_store.CategoryStore.

Fully isolated: DB-backed (plan 4a task 2) via db_isolated, a fresh database per test.
Covers: all(), get(), where(), find_by_id(), insert(), bulk_insert(),
update_by_id(), delete().

``id_field`` is ``id`` (opaque, docs/adr/2026-07-05-category-opaque-ids.md) — rows are
``{id, label, kind, color, hidden?}``.
"""

from __future__ import annotations

import pytest

from backend.db.category_store import CategoryStore


@pytest.fixture
def store(db_isolated):
    return CategoryStore()


_FOOD = {"id": "cat_13095ba70000", "label": "Food & Dining", "kind": "expense", "color": "#e0a032"}
_SALARY = {
    "id": "cat_salary",
    "label": "Salary",
    "kind": "income",
    "color": "#2ecc71",
}
_GIFT = {"id": "cat_gift", "label": "Gift Income", "kind": "income", "color": "#3498db"}
_UNCATEGORIZED = {
    "id": "cat_uncategorized",
    "label": "Uncategorized",
    "kind": "expense",
    "color": "#5a6270",
}


# ── all() / insert() ──────────────────────────────────────────────────────────


def test_empty_store_returns_empty_list(store):
    assert store.all() == []


def test_insert_and_retrieve(store):
    store.insert(_FOOD)
    rows = store.all()
    assert len(rows) == 1
    assert rows[0]["id"] == "cat_13095ba70000"


def test_bulk_insert(store):
    store.bulk_insert([_FOOD, _SALARY])
    assert len(store.all()) == 2


# ── get() ──────────────────────────────────────────────────────────────────────


def test_get_existing_id(store):
    store.insert(_FOOD)
    row = store.get("cat_13095ba70000")
    assert row is not None
    assert row["label"] == "Food & Dining"


def test_get_missing_id_returns_none(store):
    assert store.get("nonexistent") is None


def test_get_is_alias_for_find_by_id(store):
    store.insert(_FOOD)
    assert store.get("cat_13095ba70000") == store.find_by_id("cat_13095ba70000")


# ── where() ──────────────────────────────────────────────────────────────────


def test_where_kind_expense(store):
    store.bulk_insert([_FOOD, _SALARY])
    rows = store.where(kind="expense")
    assert len(rows) == 1
    assert rows[0]["id"] == "cat_13095ba70000"


def test_where_kind_income(store):
    store.bulk_insert([_FOOD, _SALARY])
    rows = store.where(kind="income")
    assert len(rows) == 1
    assert rows[0]["id"] == "cat_salary"


def test_where_no_filter_returns_all(store):
    store.bulk_insert([_FOOD, _SALARY])
    assert len(store.where()) == 2


def test_where_include_hidden_false(store):
    hidden = {**_FOOD, "hidden": True}
    store.bulk_insert([hidden, _SALARY])
    visible = store.where(include_hidden=False)
    assert len(visible) == 1
    assert visible[0]["id"] == "cat_salary"


def test_where_include_hidden_true(store):
    hidden = {**_FOOD, "hidden": True}
    store.bulk_insert([hidden, _SALARY])
    # default include_hidden=True returns everything
    assert len(store.where()) == 2


def test_where_kind_and_hidden_filter(store):
    hidden_expense = {**_FOOD, "hidden": True}
    store.bulk_insert([hidden_expense, _SALARY, _UNCATEGORIZED])
    # kind=expense, include_hidden=False → only uncategorized (not hidden)
    rows = store.where(kind="expense", include_hidden=False)
    assert len(rows) == 1
    assert rows[0]["id"] == "cat_uncategorized"


# ── update / delete ────────────────────────────────────────────────────────────


def test_update_by_id(store):
    store.insert(_FOOD)
    store.update_by_id("cat_13095ba70000", {"label": "Grub"})
    assert store.get("cat_13095ba70000")["label"] == "Grub"


def test_delete(store):
    store.insert(_FOOD)
    assert store.delete("cat_13095ba70000") is True
    assert store.get("cat_13095ba70000") is None


def test_delete_nonexistent_returns_false(store):
    assert store.delete("ghost") is False


def test_replace_all_overwrites(store):
    store.bulk_insert([_FOOD, _SALARY])
    store.replace_all([_GIFT])
    assert len(store.all()) == 1
    assert store.all()[0]["id"] == "cat_gift"
