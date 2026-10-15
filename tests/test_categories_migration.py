"""
Tests for the category-store seed / migration (RFC categories-as-data.md P0), updated for the
opaque-id contract (docs/adr/2026-07-05-category-opaque-ids.md): every row (built-in and
"legacy hybrid profile" custom category alike) is now keyed by ``id``, never ``key``.

Key invariant: the migrated store must produce the SAME effective category set as
the old profile-hybrid path for any profile state — zero behavioral change to the
current user.

We test three scenarios:
  A. Fresh instance (no profile): store is seeded from BUILTIN_CATEGORIES.
  B. Existing instance with deleted / hidden built-ins and custom categories:
     migration materialises the effective set and the set matches the old
     get_all_categories() result exactly (same ids, order is irrelevant).
  C. Existing instance with a color override: the override is applied on the
     migrated row's color field.
"""

from __future__ import annotations

import pytest

from backend.db.category_store import CategoryStore
from backend.models.category_defs import BUILTIN_CATEGORIES

# CategoryStore is DB-backed (plan 4a task 2) — every test here needs a genuinely EMPTY
# store (to test seed-from-scratch behavior), not the shared default engine tests/conftest.py
# otherwise pre-seeds with BUILTIN_CATEGORIES. db_isolated (applied to the whole module, no
# per-test store._path patching needed — the old JsonStore-era mechanism this replaces)
# redirects the process-wide DB singleton to a fresh, empty database per test.
pytestmark = pytest.mark.usefixtures("db_isolated")

# ── helpers ───────────────────────────────────────────────────────────────────


def _run_migration(store: CategoryStore, profile: dict) -> int:
    """Execute the same migration logic as main.py lifespan, returning row count."""
    if store.all():
        return 0  # already populated — idempotent

    deleted = set(profile.get("deleted_builtins") or [])
    hidden = set(profile.get("hidden_categories") or [])
    overrides = profile.get("category_color_overrides") or {}
    custom = [
        c for c in (profile.get("custom_categories") or []) if isinstance(c, dict) and c.get("id")
    ]

    eff_ids: set[str] = set()
    rows: list[dict] = []
    for c in BUILTIN_CATEGORIES:
        cid = c["id"]
        if cid in deleted:
            continue
        row = dict(c)
        if cid in overrides:
            row["color"] = overrides[cid]
        if cid in hidden:
            row["hidden"] = True
        eff_ids.add(cid)
        rows.append(row)

    for c in custom:
        cid = c.get("id")
        if not cid or cid in eff_ids:
            continue
        row = dict(c)
        if cid in hidden:
            row["hidden"] = True
        eff_ids.add(cid)
        rows.append(row)

    # Ensure only the structural anchor "split" is always present.
    # "uncategorized" has been removed as a forced sink — None is the absence of a category.
    anchor_defaults = {
        "split": ("Split", "#7c8595"),
    }
    for ak, (al, ac) in anchor_defaults.items():
        if ak not in eff_ids:
            rows.append({"id": ak, "label": al, "kind": "expense", "color": ac})

    store.bulk_insert(rows)
    return len(rows)


def _old_get_all(profile: dict, include_hidden: bool = False) -> list[dict]:
    """Re-implement the old profile-based get_all_categories logic for comparison."""
    deleted = set(profile.get("deleted_builtins") or [])
    hidden = set() if include_hidden else set(profile.get("hidden_categories") or [])
    overrides = profile.get("category_color_overrides") or {}
    custom = profile.get("custom_categories") or []

    builtin_ids = {c["id"] for c in BUILTIN_CATEGORIES}
    result = [
        {**c, "color": overrides[c["id"]]} if c["id"] in overrides else c
        for c in BUILTIN_CATEGORIES
        if c["id"] not in deleted and c["id"] not in hidden
    ]
    result += [
        c
        for c in custom
        if (c.get("id") not in builtin_ids or c.get("id") in deleted) and c.get("id") not in hidden
    ]
    return result


# ── scenario A: fresh instance ────────────────────────────────────────────────


def test_fresh_instance_seeds_all_builtins(tmp_path):
    """Seeding from BUILTIN_CATEGORIES on a fresh instance gives all built-in ids."""
    store = CategoryStore()

    store.bulk_insert(list(BUILTIN_CATEGORIES))
    rows = store.all()

    builtin_ids = {c["id"] for c in BUILTIN_CATEGORIES}
    assert {r["id"] for r in rows} == builtin_ids


def test_fresh_instance_split_sentinel_seeded(tmp_path):
    """The split sentinel must be in the seeded store."""
    store = CategoryStore()

    store.bulk_insert(list(BUILTIN_CATEGORIES))
    assert store.get("split") is not None


def test_fresh_instance_uncategorized_not_seeded(tmp_path):
    """'uncategorized' has been removed as the forced catch-all sink — it must NOT appear
    in the seeded store. None is the absence of a category; the budget shows it as 'Uncategorized'."""
    store = CategoryStore()

    store.bulk_insert(list(BUILTIN_CATEGORIES))
    assert store.get("uncategorized") is None


# ── scenario B: existing instance with hybrid profile ────────────────────────


@pytest.fixture
def hybrid_profile() -> dict:
    return {
        "base_currency": "SGD",
        "deleted_builtins": ["cat_13095ba70000", "cat_c19c4c0f0000"],
        "hidden_categories": ["cat_9bacf56e0000"],
        "custom_categories": [
            {"id": "cat_freelance", "label": "Freelance", "kind": "income", "color": "#abc123"},
            # custom shadows a deleted builtin (should win the id)
            {"id": "cat_13095ba70000", "label": "My Food", "kind": "expense", "color": "#ff0000"},
        ],
    }


def test_migration_ids_match_old_path_default(tmp_path, hybrid_profile):
    """Ids returned by the migrated store (include_hidden=False) must equal old get_all()."""
    store = CategoryStore()

    _run_migration(store, hybrid_profile)
    old = _old_get_all(hybrid_profile, include_hidden=False)

    migrated_ids = {r["id"] for r in store.where(include_hidden=False)}
    old_ids = {c["id"] for c in old}

    assert migrated_ids == old_ids, (
        f"mismatch — in migrated but not old: {migrated_ids - old_ids}, "
        f"in old but not migrated: {old_ids - migrated_ids}"
    )


def test_migration_deleted_builtin_excluded(tmp_path, hybrid_profile):
    store = CategoryStore()

    _run_migration(store, hybrid_profile)
    # "cat_c19c4c0f0000" is in deleted_builtins and has no custom shadow → must be absent.
    assert store.get("cat_c19c4c0f0000") is None


def test_migration_custom_shadows_deleted_builtin(tmp_path, hybrid_profile):
    store = CategoryStore()

    _run_migration(store, hybrid_profile)
    # "cat_13095ba70000" was deleted as a builtin but the custom category with the same id must win.
    row = store.get("cat_13095ba70000")
    assert row is not None
    assert row["label"] == "My Food"


def test_migration_hidden_flag_applied(tmp_path, hybrid_profile):
    store = CategoryStore()

    _run_migration(store, hybrid_profile)
    row = store.get("cat_9bacf56e0000")
    assert row is not None
    assert row.get("hidden") is True


def test_migration_custom_category_included(tmp_path, hybrid_profile):
    store = CategoryStore()

    _run_migration(store, hybrid_profile)
    row = store.get("cat_freelance")
    assert row is not None
    assert row["kind"] == "income"


def test_migration_anchors_always_present(tmp_path):
    """The 'split' structural anchor is always re-inserted even when deleted_builtins lists it.
    'uncategorized' is no longer an anchor — it stays absent after migration."""
    store = CategoryStore()

    profile = {"deleted_builtins": ["uncategorized", "split"]}
    _run_migration(store, profile)

    # "split" is the only structural anchor — migration re-inserts it.
    assert store.get("split") is not None
    # "uncategorized" is no longer an anchor and remains absent.
    assert store.get("uncategorized") is None


def test_migration_idempotent(tmp_path, hybrid_profile):
    """Re-running migration when the store is already populated is a no-op."""
    store = CategoryStore()

    n1 = _run_migration(store, hybrid_profile)
    count_after_first = len(store.all())
    assert n1 == count_after_first  # first run materialises every effective row

    n2 = _run_migration(store, hybrid_profile)
    assert n2 == 0  # second run is a no-op
    assert len(store.all()) == count_after_first


# ── scenario C: color override ────────────────────────────────────────────────


def test_migration_color_override_applied(tmp_path):
    store = CategoryStore()

    profile = {"category_color_overrides": {"cat_13095ba70000": "#cafeba"}}
    _run_migration(store, profile)

    row = store.get("cat_13095ba70000")
    assert row is not None
    assert row["color"] == "#cafeba"
