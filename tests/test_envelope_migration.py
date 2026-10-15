"""
Unit tests for services.envelopes.ensure_envelopes_migrated — the idempotent first-boot
seed that mints one 1:1 envelope per currently-budgeted category so a fresh install's
budget is immediately usable (.claude/rfcs/envelope-not-category.md §4).

Money (assigned amounts, targets, notes) is ENVELOPE-ID NATIVE end to end
(docs/adr/2026-07-05-envelope-id-native.md) — there is no category-keyed storage
representation to re-key here. The old dual-phase migration (mint envelopes AND re-key a
still-category-keyed book) plus its grandfather resyncs (``resync_legacy_category_keyed_
assignments``, ``resync_orphaned_budgeted_categories``, ``stamp_missing_envelope_placement``,
``resync_legacy_category_keyed_targets``) is GONE — those functions no longer exist. A
one-shot migration (since removed) re-keyed the one real book directly;
runtime never carries that fallback.

Fully isolated + offline: assignments store + profile.json are monkeypatched into a pytest
tmp_path (mirrors tests/test_categories_service.py's env fixture); categories/groups/
envelopes are DB-backed (plan 4a task 2) and share the process-wide default database, reset
per test by tests/conftest.py's autouse fixtures — ``cats_path``/``groups_path``/
``envelopes_path`` are no longer real files, just identity sentinels ``write``/``read``
dispatch on, so every existing ``env.write(env.envelopes_path, rows)`` call site keeps
working unchanged, routed to the store instead of a raw file write.
"""

from __future__ import annotations

import json

import pytest

from backend.db.category_store import CategoryStore
from backend.db.envelope_groups_store import EnvelopeGroupsStore
from backend.db.envelope_store import EnvelopeStore
from backend.db.profile_store import ProfileStore
from backend.services import envelopes as envelopes_service


@pytest.fixture
def env(tmp_path, monkeypatch):
    cats_path = tmp_path / "categories.json"
    groups_path = tmp_path / "category_groups.json"
    assign_path = tmp_path / "budget_assignments.json"
    envelopes_path = tmp_path / "envelopes.json"
    profile_path = tmp_path / "profile.json"

    profile_store = ProfileStore()

    def _replace_assignments(data):
        # BudgetAssignmentsStore (plan 4a task 4) is DB-backed with no bulk replace_all —
        # mirror EnvelopeStore.replace_all's wipe+insert semantics via its real read/write
        # API (exact-zero is this store's own delete, per its sparse-file invariant).
        store = envelopes_service.assignments_store
        for month, cats in store.all_map().items():
            for category in cats:
                store.set_assigned(month, category, 0.0)
        for month, cats in (data or {}).items():
            for category, amount in cats.items():
                store.set_assigned(month, category, amount)

    def _write(path, data):
        if path is cats_path:
            CategoryStore().replace_all(data)
        elif path is groups_path:
            EnvelopeGroupsStore().replace_all(data)
        elif path is envelopes_path:
            EnvelopeStore().replace_all(data)
        elif path is assign_path:
            _replace_assignments(data)
        elif path is profile_path:
            profile_store.set_raw(data)
        else:
            path.write_text(json.dumps(data), encoding="utf-8")

    def _read(path):
        if path is cats_path:
            return CategoryStore().all()
        if path is groups_path:
            return EnvelopeGroupsStore().all()
        if path is envelopes_path:
            return EnvelopeStore().all()
        if path is assign_path:
            return envelopes_service.assignments_store.all_map()
        if path is profile_path:
            return profile_store.get_raw()
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None

    def _set_profile(**keys):
        cur = _read(profile_path) or {}
        cur.update(keys)
        _write(profile_path, cur)

    return type(
        "Env",
        (),
        {
            "cats_path": cats_path,
            "groups_path": groups_path,
            "assign_path": assign_path,
            "envelopes_path": envelopes_path,
            "profile_path": profile_path,
            "write": staticmethod(_write),
            "read": staticmethod(_read),
            "set_profile": staticmethod(_set_profile),
        },
    )


# ── Pre-migration book fixture data ────────────────────────────────────────────────

_CATEGORIES = [
    {"id": "cat_a31b2c3d4001", "label": "Food & Dining", "kind": "expense", "color": "#e0a032"},
    {"id": "cat_a31b2c3d4002", "label": "Transport", "kind": "expense", "color": "#3b86dd"},
    {"id": "cat_f2ff09940000", "label": "Shopping", "kind": "expense", "color": "#9a7fd0"},
    {"id": "salary", "label": "Salary", "kind": "income", "color": "#2ecc71"},
    # Always present per BUILTIN_CATEGORIES — must never get an envelope of its own.
    {"id": "split", "label": "Split", "kind": "expense", "color": "#7c8595"},
]

_GROUPS = [
    {
        "id": "grp_bills",
        "name": "Bills",
        "collapsed": False,
        "order": 0,
    },
    {
        "id": "grp_wants",
        "name": "Wants",
        "collapsed": False,
        "order": 1,
    },
]

# "misc_stale" is budgeted (carries an assignment) but belongs to no group and has no
# expense-category / target entry — exercises "a cat with no envelope gets one minted".
_ASSIGNMENTS = {
    "2026-06": {
        "cat_a31b2c3d4001": 500.0,
        "cat_a31b2c3d4002": 100.0,
        "cat_f2ff09940000": 50.0,
        "misc_stale": 25.0,
    },
    "2026-07": {"cat_a31b2c3d4001": 500.0, "cat_a31b2c3d4002": 120.0},
}

_TARGETS = {
    "cat_a31b2c3d4001": {"type": "monthly", "amount": 500.0},
    "cat_f2ff09940000": {"type": "by_date", "amount": 200.0, "date": "2026-12-31"},
}

_NOTES = {
    "cat_a31b2c3d4001": "grocery budget",
    "cat_a31b2c3d4002": "commute + rideshare",
}


def _seed_pre_migration_book(env):
    env.write(env.cats_path, _CATEGORIES)
    env.write(env.groups_path, _GROUPS)
    env.write(env.assign_path, _ASSIGNMENTS)
    env.write(
        env.profile_path,
        {
            "base_currency": "SGD",
            "category_targets": _TARGETS,
            "category_notes": _NOTES,
        },
    )


def _envelopes_by_category(env):
    """1:1-migrated envelopes keyed by their single category key."""
    return {e["category_ids"][0]: e for e in EnvelopeStore().all() if len(e["category_ids"]) == 1}


# ── Migration behavior ──────────────────────────────────────────────────────────────


def test_migration_mints_1to1_envelopes_preserving_group_and_order(env):
    """The seed mints one envelope per budgeted category, placing it by the starter template
    (``STARTER_GROUPS``: category → group NAME; no group lists categories) in template order — money itself (assigned amounts, targets, notes) is
    NOT carried onto the envelope object (envelope-id-native storage lives in the
    assignments/profile stores, keyed by the newly-minted id, never on the envelope row
    itself — docs/adr/2026-07-05-envelope-id-native.md)."""
    _seed_pre_migration_book(env)

    result = envelopes_service.ensure_envelopes_migrated()
    assert result["migrated"] is True

    by_cat = _envelopes_by_category(env)
    assert set(by_cat) == {"cat_a31b2c3d4001", "cat_a31b2c3d4002", "cat_f2ff09940000", "misc_stale"}

    food, transport, shopping, misc = (
        by_cat["cat_a31b2c3d4001"],
        by_cat["cat_a31b2c3d4002"],
        by_cat["cat_f2ff09940000"],
        by_cat["misc_stale"],
    )

    # Template order: Bills (food, transport) before Wants (shopping); a category outside the
    # template "misc_stale" falls into Others last.
    others_id = envelopes_service.groups_store.ensure_others()["id"]
    assert food["group_id"] == "grp_bills"
    assert transport["group_id"] == "grp_bills"
    assert shopping["group_id"] == "grp_wants"
    assert misc["group_id"] == others_id
    assert food["order"] < transport["order"] < shopping["order"] < misc["order"]

    # Name = category label, fallback to key.
    assert food["name"] == "Food & Dining"
    assert misc["name"] == "misc_stale"  # not in CategoryStore -> key fallback

    for e in by_cat.values():
        assert e["id"].startswith("env_")
        assert e["archived"] is False


def test_split_sentinel_never_gets_an_envelope(env):
    _seed_pre_migration_book(env)
    envelopes_service.ensure_envelopes_migrated()
    by_cat = _envelopes_by_category(env)
    assert "split" not in by_cat


def test_migration_leaves_assignments_targets_notes_in_place(env):
    """The seed only mints envelope ROWS — it never touches the assignments store or
    profile.json. A category's pre-existing bare-keyed money (a synthetic scenario at this
    point: the one real book was migrated to envelope-id-native storage directly by a
    since-removed one-shot script, never through this runtime path) is left
    exactly where it was; it simply isn't visible under the newly-minted envelope's id."""
    _seed_pre_migration_book(env)
    before_assign = env.read(env.assign_path)

    envelopes_service.ensure_envelopes_migrated()

    assert env.read(env.assign_path) == before_assign
    profile = env.read(env.profile_path)
    assert profile["category_targets"] == _TARGETS
    assert profile["category_notes"] == _NOTES


def test_second_run_is_a_total_noop(env):
    _seed_pre_migration_book(env)
    envelopes_service.ensure_envelopes_migrated()

    envelopes_snapshot = EnvelopeStore().all()
    assignments_snapshot = envelopes_service.assignments_store.all_map()
    groups_snapshot = EnvelopeGroupsStore().all()

    result2 = envelopes_service.ensure_envelopes_migrated()
    assert result2 == {"migrated": False, "reason": "already migrated"}

    assert EnvelopeStore().all() == envelopes_snapshot
    assert envelopes_service.assignments_store.all_map() == assignments_snapshot
    assert EnvelopeGroupsStore().all() == groups_snapshot


def test_already_migrated_book_is_untouched(env):
    _seed_pre_migration_book(env)
    # Simulate an instance that already has envelopes.json populated (e.g. seeded another
    # way) — the migration must not run at all, regardless of whether the pre-existing
    # envelope set matches what a fresh migration would produce.
    env.write(
        env.envelopes_path,
        [
            {
                "id": "env_manual",
                "name": "Manual",
                "category_ids": ["cat_a31b2c3d4001"],
                "group_id": None,
                "order": 0,
                "archived": False,
            }
        ],
    )
    before_assign = envelopes_service.assignments_store.all_map()
    before_groups = EnvelopeGroupsStore().all()

    result = envelopes_service.ensure_envelopes_migrated()

    assert result == {"migrated": False, "reason": "already migrated"}
    assert envelopes_service.assignments_store.all_map() == before_assign
    assert EnvelopeGroupsStore().all() == before_groups
    envelopes = env.read(env.envelopes_path)
    assert len(envelopes) == 1
    assert envelopes[0]["id"] == "env_manual"


def test_envelopes_present_with_category_keyed_assignments_leaves_assignments_untouched(env):
    """Regression: envelopes.json already populated (e.g. a hand-recovered / rolled-back
    instance) but budget_assignments.json is STILL category-keyed — the trigger guard is
    "envelopes.json non-empty", full stop; it must never re-key assignments again just
    because their keys don't look like envelope ids yet. Mirrors the exact state produced by
    manually reversing a live re-key: envelopes exist, assignments were restored to
    category keys, and nothing should touch them until a caller re-keys explicitly."""
    _seed_pre_migration_book(env)
    env.write(
        env.envelopes_path,
        [
            {
                "id": "env_food",
                "name": "Food & Dining",
                "category_ids": ["cat_a31b2c3d4001"],
                "group_id": "grp_bills",
                "order": 0,
                "archived": False,
            }
        ],
    )
    # Assignments deliberately left category-keyed (the recovered state), NOT env_-keyed.
    before_assign = envelopes_service.assignments_store.all_map()
    before_groups = EnvelopeGroupsStore().all()

    result = envelopes_service.ensure_envelopes_migrated()

    assert result == {"migrated": False, "reason": "already migrated"}
    assert envelopes_service.assignments_store.all_map() == before_assign
    assert EnvelopeGroupsStore().all() == before_groups
    after_assign = env.read(env.assign_path)
    assert after_assign["2026-06"]["cat_a31b2c3d4001"] == 500.0  # still category-keyed, untouched


def test_migration_noop_when_nothing_to_migrate(env):
    # A non-empty category store (so `expense_categories()` uses it, not the
    # BUILTIN_CATEGORIES fallback) with only an income-kind category, no groups, no
    # assignments, no targets — the budgeted universe is genuinely empty.
    env.write(env.cats_path, [{"id": "salary", "label": "Salary", "kind": "income"}])
    env.write(env.groups_path, [])
    env.write(env.assign_path, {})
    env.write(env.profile_path, {"base_currency": "SGD"})

    result = envelopes_service.ensure_envelopes_migrated()
    assert result == {"migrated": False, "reason": "nothing to migrate"}
    assert env.read(env.envelopes_path) is None or env.read(env.envelopes_path) == []


# ── Numbers invariant (pinned) ────────────────────────────────────────────────────
# A book minted fresh (no assignments/targets predate the envelope) computes exactly the
# same figures a category-keyed book always did — same rows, same labels, same figures —
# and the whole boot step (now just ``ensure_envelopes_migrated``, the grandfather resyncs
# are gone per docs/adr/2026-07-05-envelope-id-native.md) is idempotent.


@pytest.fixture
def full_env(tmp_path, monkeypatch, db_isolated):
    from backend.db.account_store import AccountStore
    from backend.db.transaction_store import TransactionStore
    from backend.services import ledger as svc
    from backend.services import planning as planning_svc

    monkeypatch.setattr(planning_svc, "load_fx_cache", lambda: {"SGD": 1.0})
    monkeypatch.setattr(svc, "base_currency", lambda: "SGD")

    ProfileStore().set_raw({"base_currency": "SGD"})
    AccountStore().replace_all(
        [
            {
                "id": "bank",
                "name": "Bank",
                "kind": "asset",
                "type": "cash",
                "currency": "SGD",
                "initial_balance": 0.0,
            }
        ]
    )
    TransactionStore().replace_all(
        [
            {
                "id": "txn_income_1",
                "kind": "income",
                "date": "2026-06-01",
                "category": "",
                "to_account_id": "bank",
                "to_amount": 1000.0,
                "to_currency": "SGD",
            },
            {
                "id": "txn_expense_1",
                "kind": "expense",
                "date": "2026-06-10",
                "category": "cat_a31b2c3d4001",
                "from_account_id": "bank",
                "from_amount": 200.0,
                "from_currency": "SGD",
            },
        ]
    )
    CategoryStore().replace_all(
        [{"id": "cat_a31b2c3d4001", "label": "Food & Dining", "kind": "expense"}]
    )
    EnvelopeGroupsStore().replace_all([])
    return tmp_path


def test_full_split_migration_is_byte_identical_for_all_1to1_book(full_env, monkeypatch):
    """The pinned invariant: on a book that ends up all-1:1 (no zero-category envelopes, no
    orphans, no unbudgeted lines), the fresh-install seed (money assigned by envelope id,
    envelope-id-native from the write path up) produces a normal-looking budget table — same
    rows, same labels, same figures a pre-full-split, category-keyed book would have — and
    running it twice is a no-op."""
    from backend.services import planning as planning_svc

    envelopes_service.ensure_envelopes_migrated()
    food_env_id = envelopes_service.env_of()["cat_a31b2c3d4001"]
    planning_svc.assignments_store.set_assigned("2026-06", food_env_id, 500.0)

    table = planning_svc.budget_month("2026-06")

    def _row(key):
        for g in table["groups"]:
            for c in g["categories"]:
                if c["key"] == key:
                    return c
        raise AssertionError(f"{key} not in table")

    food = _row(food_env_id)
    assert food["label"] == "Food & Dining"
    assert food["assigned"] == 500.0
    assert food["activity"] == -200.0
    assert food["available"] == 300.0
    assert food["category_ids"] == ["cat_a31b2c3d4001"]
    assert food["envelope_id"] == food_env_id

    assert table["on_budget_cash"] == 800.0  # 1000 income − 200 expense
    assert table["ready_to_assign"] == 500.0  # 800 cash − 300 reserved
    assert table["totals"]["assigned"] == 500.0
    assert table["totals"]["activity"] == -200.0
    assert table["totals"]["available"] == 300.0
    # No Unbudgeted/Uncategorized noise on a fully-linked, fully-migrated book.
    assert not any(g.get("unbudgeted") or g.get("unsorted") for g in table["groups"])

    # Re-running the seed is a total no-op (already-migrated guard).
    before = EnvelopeStore().all()
    before_assign = planning_svc.assignments_store.all_map()
    assert envelopes_service.ensure_envelopes_migrated() == {
        "migrated": False,
        "reason": "already migrated",
    }
    assert EnvelopeStore().all() == before
    assert planning_svc.assignments_store.all_map() == before_assign
    assert planning_svc.budget_month("2026-06") == table
