"""CategoryStore — DB-backed (plan 4a task 2), on BookPayloadTableStore. Seeding
(BUILTIN_CATEGORIES on a fresh instance) is handled by the caller in backend/main.py,
unaffected by this rewrite — not re-tested here.
"""

from __future__ import annotations

from backend.db.category_store import CategoryStore

_FOOD = {"id": "cat_food", "label": "Food & Dining", "kind": "expense", "color": "#e0a032"}
_SALARY = {
    "id": "cat_salary",
    "label": "Salary",
    "kind": "income",
    "color": "#2ecc71",
}


def test_insert_find_update_delete_round_trip(db_engine):
    store = CategoryStore(engine=db_engine)
    store.insert(dict(_FOOD))
    assert store.find_by_id("cat_food")["label"] == "Food & Dining"
    store.update_by_id("cat_food", {"label": "Grub"})
    assert store.get("cat_food")["label"] == "Grub"
    assert store.delete("cat_food") is True
    assert store.get("cat_food") is None


def test_where_filters_by_kind_and_hidden(db_engine):
    store = CategoryStore(engine=db_engine)
    store.bulk_insert([dict(_FOOD), dict(_SALARY), {**_FOOD, "id": "cat_hidden", "hidden": True}])
    assert {r["id"] for r in store.where(kind="income")} == {"cat_salary"}
    visible_ids = {r["id"] for r in store.where(include_hidden=False)}
    assert "cat_hidden" not in visible_ids
    assert "cat_food" in visible_ids
