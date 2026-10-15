"""
Envelope≠category C-P1/P2 — the assignment-storage flip (RFC envelope-not-category.md).

Post-migration, assigned amounts are stored keyed by ENVELOPE id; the table/API stay
category-keyed through the 1:1 facade. These tests run planning in MIGRATED mode (seeded
envelopes + env-keyed assignments) and assert: numbers identical to the category-keyed
twin, writes land env-keyed, legacy months still read, category rename/merge/delete keep
the mapping true, and env ids never leak into the category universe.
"""

from __future__ import annotations

import json

import pytest

from backend.core.errors import ConflictError, NotFoundError, ValidationError
from backend.db.category_store import CategoryStore
from backend.services import budgets as budgets_service
from backend.services import categories as categories_service
from backend.services import envelopes as envelopes_service
from backend.services import ledger as svc
from backend.services import planning as planning

_FX = {"SGD": 1.0}

_ENV_FOOD = {
    "id": "env_food0000001",
    "name": "Food",
    "category_ids": ["cat_13095ba70000"],
    "group_id": "others",
    "order": 0,
    "archived": False,
}
_ENV_TRANSPORT = {
    "id": "env_trans000001",
    "name": "Transport",
    "category_ids": ["cat_db5642ab0000"],
    "group_id": "others",
    "order": 1,
    "archived": False,
}


@pytest.fixture
def env(tmp_path, monkeypatch, db_isolated):
    assign_path = tmp_path / "budget_assignments.json"
    groups_path = tmp_path / "category_groups.json"
    envelopes_path = tmp_path / "envelopes.json"

    monkeypatch.setattr(planning, "load_fx_cache", lambda: dict(_FX))
    monkeypatch.setattr(svc, "base_currency", lambda: "SGD")
    # budget_assignments is DB-backed now too (plan 4a task 4) — db_isolated already redirects
    # planning.assignments_store / envelopes_service.assignments_store (module singletons) to
    # this same fresh, empty per-test database; ``assign_path`` below stays only as an identity
    # sentinel for ``_w``/the read helper, exactly like ``envelopes_path``/``groups_path``.
    # CategoryStore is DB-backed (plan 4a task 2) and db_isolated redirects the process-wide
    # DB singleton for THIS test — the shared conftest.py pre-seed (tests/conftest.py's
    # _reset_global_fx_and_profile) runs BEFORE db_isolated activates (autouse fixtures set up
    # before explicitly-requested ones at the same scope), so it seeds the OLD, now-orphaned
    # engine, not this one. Reseed here so "cat_13095ba70000"/"cat_db5642ab0000"/etc. — used
    # as bare envelope-side keys throughout this file, never actually created via
    # create_category (this file's long-standing pattern) — resolve against THIS isolated DB.
    from backend.db.category_store import CategoryStore
    from backend.models.category_defs import BUILTIN_CATEGORIES

    CategoryStore().replace_all(list(BUILTIN_CATEGORIES))
    # accounts/transactions are DB-backed now (plan 3 task 4); category_groups/envelopes are
    # DB-backed too (plan 4a task 2) — db_isolated redirects the process-wide DB singleton, so
    # every module's store instance (svc.accounts/svc.transactions, categories_service's own
    # transaction_store, envelopes_service.envelope_store, categories_service.groups_store, ...)
    # resolves against the same isolated database, with no per-instance path-sync needed.

    from backend.db.profile_store import ProfileStore

    ProfileStore().set_raw({"base_currency": "SGD"})
    svc.accounts.replace_all(
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

    def _replace_assignments(by_month):
        # BudgetAssignmentsStore (plan 4a task 4) is DB-backed with no bulk replace_all —
        # mirror EnvelopeStore.replace_all's wipe+insert semantics via its real read/write
        # API (exact-zero is this store's own delete, per its sparse-file invariant).
        store = envelopes_service.assignments_store
        for month, cats in store.all_map().items():
            for category in cats:
                store.set_assigned(month, category, 0.0)
        for month, cats in (by_month or {}).items():
            for category, amount in cats.items():
                store.set_assigned(month, category, amount)

    def _w(path, obj):
        """``envelopes_path``/``groups_path``/``assign_path`` are no longer real files
        (envelopes/category_groups are DB-backed since plan 4a task 2, budget_assignments
        since plan 4a task 4) — they stay as identity sentinels so every existing
        ``env.w(env.envelopes_path, rows)``/``env.w(env.assign_path, by_month)`` call site
        below keeps working unchanged, routed to the real store instead of a raw file write."""
        if path == envelopes_path:
            envelopes_service.envelope_store.replace_all(obj)
        elif path == groups_path:
            categories_service.groups_store.replace_all(obj)
        elif path == assign_path:
            _replace_assignments(obj)
        else:
            path.write_text(json.dumps(obj), encoding="utf-8")

    def _r(path):
        """Complement to ``_w`` for the one sentinel callers also READ back directly
        (``env.assign_path.read_text()`` throughout this file) — returns the same
        ``{month: {category: amount}}`` shape ``json.loads`` used to."""
        if path == assign_path:
            return envelopes_service.assignments_store.all_map()
        return json.loads(path.read_text(encoding="utf-8"))

    def _set_txns(rows):
        from backend.core.ids import new_id
        from tests.conftest import seed_stub_accounts

        # id is now a real (NOT NULL) DB primary key — mint one for any row that doesn't
        # already carry an explicit id (this file's _income/_expense helpers never set one),
        # same as a real create would.
        rows = [r if r.get("id") else {**r, "id": new_id("txn")} for r in rows]
        seed_stub_accounts(rows)
        svc.transactions.replace_all(rows)

    return type(
        "Env",
        (),
        {
            "w": staticmethod(_w),
            "r": staticmethod(_r),
            "set_txns": staticmethod(_set_txns),
            "assign_path": assign_path,
            "envelopes_path": envelopes_path,
            "groups_path": groups_path,
        },
    )


def _income(amount=1000.0):
    return {
        "kind": "income",
        "date": "2026-06-01",
        "category": "",
        "to_account_id": "bank",
        "to_amount": amount,
        "to_currency": "SGD",
    }


def _expense(cat, amount):
    return {
        "kind": "expense",
        "date": "2026-06-10",
        "category": cat,
        "from_account_id": "bank",
        "from_amount": amount,
        "from_currency": "SGD",
    }


def _row(table, key):
    """Look up a rendered row by its own (envelope) key OR by category membership — most
    callers pass a member category ("cat_13095ba70000"), a few pass a zero-category envelope's own id
    directly (docs/adr/2026-07-05-envelope-id-native.md)."""
    for g in table["groups"]:
        for c in g["categories"]:
            if c["key"] == key or key in (c.get("category_ids") or []):
                return c
    raise AssertionError(f"{key} not in table")


def _seed_migrated(env, assigned_food=300.0):
    """A migrated book: envelopes stored, assignments ENV-keyed."""
    env.w(env.envelopes_path, [_ENV_FOOD, _ENV_TRANSPORT])
    env.w(env.assign_path, {"2026-06": {"env_food0000001": assigned_food}})
    env.set_txns([_income(1000.0), _expense("cat_13095ba70000", 200.0)])


def test_migrated_book_renders_correctly(env):
    """Superseded by the full split (RFC envelope-category-full-split.md §Backend 3): a
    "category-keyed twin" with envelopes.json wiped to [] no longer renders ANY row for
    "cat_13095ba70000" — the pseudo-row family that used to make the two representations equivalent is
    gone. What stays true: a migrated (real 1:1 envelope) book renders "cat_13095ba70000" correctly. The
    all-1:1 byte-identity guarantee itself is pinned in
    tests/test_envelope_migration.py::test_full_split_migration_is_byte_identical_for_all_1to1_book."""
    _seed_migrated(env)
    migrated = planning.budget_month("2026-06")
    assert _row(migrated, "cat_13095ba70000")["assigned"] == 300.0
    assert _row(migrated, "cat_13095ba70000")["available"] == 100.0  # 300 − 200


def test_assign_writes_land_env_keyed(env):
    _seed_migrated(env)
    planning.apply_budget_change(
        {"type": "assign", "month": "2026-06", "envelope_id": "env_food0000001", "amount": 450.0}
    )
    raw = env.r(env.assign_path)
    assert raw["2026-06"]["env_food0000001"] == 450.0
    assert "cat_13095ba70000" not in raw["2026-06"]  # never a category-keyed twin entry


def test_legacy_category_keyed_month_still_reads(env):
    env.w(env.envelopes_path, [_ENV_FOOD])
    env.w(env.assign_path, {"2026-05": {"cat_13095ba70000": 120.0}})  # written pre-migration
    env.set_txns([_income(1000.0)])
    assert planning._assigned_amt("2026-05", "cat_13095ba70000") == 120.0


def test_move_money_stays_env_keyed_and_conserves(env):
    _seed_migrated(env)
    planning.apply_budget_change(
        {
            "type": "move_money",
            "month": "2026-06",
            "from_envelope_id": "env_food0000001",
            "to_envelope_id": "env_trans000001",
            "amount": 100.0,
        }
    )
    raw = env.r(env.assign_path)["2026-06"]
    assert raw["env_food0000001"] == 200.0
    assert raw["env_trans000001"] == 100.0
    assert round(sum(raw.values()), 2) == 300.0


def test_new_category_has_no_envelope_and_cannot_be_assigned(env):
    """Full split (RFC envelope-category-full-split.md §Backend 2/3): create_category NEVER
    mints an envelope, on ANY book — the old "unmigrated book falls back to a cat-keyed
    pseudo-row" behavior is gone too, since pseudo-rows themselves are gone. A fresh category
    has no budget row, so assign/set_target/move_money REFUSE it outright."""
    env.w(env.envelopes_path, [])
    env.w(env.assign_path, {})
    env.set_txns([])
    created = categories_service.create_category("Startup", "expense")
    cat_id = created["id"]
    assert envelopes_service.env_of().get(cat_id) is None
    with pytest.raises(ValidationError):
        planning.apply_budget_change(
            {"type": "assign", "month": "2026-06", "envelope_id": cat_id, "amount": 50.0}
        )
    with pytest.raises(AssertionError):  # no row at all, not even a zero one
        _row(planning.budget_month("2026-06"), cat_id)


def test_rename_category_keeps_assignment_resolution(env):
    """Superseded (ids are immutable now — RFC category-opaque-ids, no more rekey): a category
    'rename' is a pure relabel that keeps the SAME id, so assigned money's resolution is
    unaffected — pinned here as a regression guard (a relabel implemented as delete+recreate
    would break this)."""
    _seed_migrated(env)
    categories_service.rename_category("cat_13095ba70000", new_label="Groceries")
    table = planning.budget_month("2026-06")
    row = _row(table, "cat_13095ba70000")
    assert row["assigned"] == 300.0  # env-keyed money untouched by a pure relabel
    envs = envelopes_service.envelope_store.all()
    food_env = next(e for e in envs if e["id"] == "env_food0000001")
    assert food_env["category_ids"] == ["cat_13095ba70000"]  # id never changes — no more rekey


def test_merge_categories_folds_envelopes(env):
    env.w(env.envelopes_path, [_ENV_FOOD, _ENV_TRANSPORT])
    env.w(env.assign_path, {"2026-06": {"env_food0000001": 300.0, "env_trans000001": 100.0}})
    env.set_txns([_income(1000.0)])
    categories_service.rename_category("cat_13095ba70000", merge_into="cat_db5642ab0000")
    raw = env.r(env.assign_path)["2026-06"]
    assert raw.get("env_trans000001") == 400.0  # folded, conserved
    assert not raw.get("env_food0000001")
    envs = envelopes_service.envelope_store.all()
    # Hard-deleted for real (ADR 2026-09-26-deleting-an-envelope-deletes-it) — no more archive.
    assert not any(e["id"] == "env_food0000001" for e in envs)


def test_delete_category_hard_deletes_envelope_and_releases_money(env):
    _seed_migrated(env, assigned_food=300.0)
    env.set_txns([_income(1000.0)])  # no food spend → deletable without reassign
    before = planning.budget_month("2026-06")["ready_to_assign"]
    categories_service.delete_category("cat_13095ba70000")
    raw = env.r(env.assign_path).get("2026-06", {})
    assert not raw.get("env_food0000001")  # zeroed (month may prune away entirely)
    envs = envelopes_service.envelope_store.all()
    # Hard-deleted for real (ADR 2026-09-26-deleting-an-envelope-deletes-it) — no more archive.
    assert not any(e["id"] == "env_food0000001" for e in envs)
    after = planning.budget_month("2026-06")["ready_to_assign"]
    assert after == round(before + 300.0, 2)  # the reserve released back to RTA


# ── Deleting a category CARRIES its assigned money to the reassign target ─────────
# Confirmed against YNAB: deleting a category that has money assigned, while reassigning its
# transactions to another category, moves the ASSIGNED figure onto that target too — Ready to
# Assign does not move. Ours released it to RTA instead, so RTA jumped by the deleted
# category's Assigned while the spending it covered simply moved house.

_NOW = planning._this_month()


def _income_now(amount):
    return dict(_income(amount), date=f"{_NOW}-01")


def _expense_now(cat, amount):
    return dict(_expense(cat, amount), date=f"{_NOW}-10")


def test_delete_category_carries_assigned_to_the_reassign_target(env):
    """The exact repro: Transport (Assigned 50, Activity -82.30, Available -32.30) is deleted
    and its one transaction reassigned to Food & Dining (Assigned 0). Food ends up with the 50
    and Ready to Assign does not move."""
    env.w(env.envelopes_path, [_ENV_FOOD, _ENV_TRANSPORT])
    env.w(env.assign_path, {_NOW: {"env_trans000001": 50.0}})
    env.set_txns([_income_now(1000.0), _expense_now("cat_db5642ab0000", 82.30)])

    before = planning.budget_month(_NOW)
    assert _row(before, "cat_db5642ab0000")["assigned"] == 50.0
    assert _row(before, "cat_db5642ab0000")["available"] == -32.30
    assert _row(before, "cat_13095ba70000")["assigned"] == 0.0

    result = categories_service.delete_category(
        "cat_db5642ab0000", reassign=True, reassign_to="cat_13095ba70000"
    )
    assert result["carried_assigned"] == 50.0

    after = planning.budget_month(_NOW)
    food = _row(after, "cat_13095ba70000")
    assert food["assigned"] == 50.0  # the 50 followed the activity, not released to RTA
    assert food["activity"] == -82.30
    assert food["available"] == -32.30
    assert after["ready_to_assign"] == before["ready_to_assign"]
    # The money moved, it was not duplicated: the emptied envelope holds nothing.
    assert not env.r(env.assign_path).get(_NOW, {}).get("env_trans000001")


def test_carry_adds_to_what_the_target_already_had(env):
    """The target keeps its own assignment — the carry is an ADD, never an overwrite."""
    env.w(env.envelopes_path, [_ENV_FOOD, _ENV_TRANSPORT])
    env.w(env.assign_path, {_NOW: {"env_trans000001": 50.0, "env_food0000001": 120.0}})
    env.set_txns([_income_now(1000.0), _expense_now("cat_db5642ab0000", 82.30)])

    before = planning.budget_month(_NOW)["ready_to_assign"]
    categories_service.delete_category(
        "cat_db5642ab0000", reassign=True, reassign_to="cat_13095ba70000"
    )
    after_table = planning.budget_month(_NOW)
    assert _row(after_table, "cat_13095ba70000")["assigned"] == 170.0
    assert after_table["ready_to_assign"] == before


def test_carry_moves_this_month_only_never_rewrites_budget_history(env):
    """A deletion is a forward-state edit. An earlier month's assignment still releases exactly
    as it always did — only the current month follows the reassignment."""
    env.w(env.envelopes_path, [_ENV_FOOD, _ENV_TRANSPORT])
    env.w(
        env.assign_path,
        {"2026-06": {"env_trans000001": 30.0}, _NOW: {"env_trans000001": 50.0}},
    )
    env.set_txns([_income_now(1000.0)])

    result = categories_service.delete_category(
        "cat_db5642ab0000", reassign=True, reassign_to="cat_13095ba70000"
    )
    assert result["carried_assigned"] == 50.0
    raw = env.r(env.assign_path)
    assert not raw.get("2026-06", {}).get("env_food0000001")  # history untouched
    assert not raw.get("2026-06", {}).get("env_trans000001")  # released, as before
    assert raw[_NOW]["env_food0000001"] == 50.0


def test_no_reassign_target_still_releases_to_ready_to_assign(env):
    """The explicit "leave it Uncategorized" choice: there is no envelope to carry to, so the money
    releases exactly as it did before the carry existed."""
    env.w(env.envelopes_path, [_ENV_FOOD, _ENV_TRANSPORT])
    env.w(env.assign_path, {_NOW: {"env_trans000001": 50.0}})
    env.set_txns([_income_now(1000.0)])

    before = planning.budget_month(_NOW)["ready_to_assign"]
    result = categories_service.delete_category("cat_db5642ab0000")
    assert result["carried_assigned"] == 0.0
    after = planning.budget_month(_NOW)
    assert after["ready_to_assign"] == round(before + 50.0, 2)
    assert _row(after, "cat_13095ba70000")["assigned"] == 0.0


def test_unbudgeted_reassign_target_has_nowhere_to_land_so_it_releases(env):
    """A category with no envelope of its own is a pure transaction label — there is no pot to
    carry money into, so this falls back to releasing (we do not invent a bucket for it)."""
    env.w(env.envelopes_path, [_ENV_FOOD, _ENV_TRANSPORT])
    env.w(env.assign_path, {_NOW: {"env_trans000001": 50.0}})
    env.set_txns([_income_now(1000.0)])
    assert "cat_dfcf9f910000" not in envelopes_service.env_of()  # Entertainment: unlinked

    before = planning.budget_month(_NOW)["ready_to_assign"]
    result = categories_service.delete_category(
        "cat_db5642ab0000", reassign=True, reassign_to="cat_dfcf9f910000"
    )
    assert result["carried_assigned"] == 0.0
    assert planning.budget_month(_NOW)["ready_to_assign"] == round(before + 50.0, 2)


def test_a_surviving_shared_envelope_carries_nothing(env):
    """Envelope and category are not 1:1. Deleting ONE of several categories mapped to an
    envelope leaves that envelope alive and holding its money — nothing was released, so there
    is nothing to carry, and the figure must not be doubled onto itself."""
    shared = dict(_ENV_TRANSPORT, category_ids=["cat_db5642ab0000", "cat_dfcf9f910000"])
    env.w(env.envelopes_path, [_ENV_FOOD, shared])
    env.w(env.assign_path, {_NOW: {"env_trans000001": 50.0}})
    env.set_txns([_income_now(1000.0)])

    before = planning.budget_month(_NOW)["ready_to_assign"]
    result = categories_service.delete_category(
        "cat_db5642ab0000", reassign=True, reassign_to="cat_dfcf9f910000"
    )
    assert result["carried_assigned"] == 0.0
    envelope = envelopes_service.envelope_store.find_by_id("env_trans000001")
    assert envelope["archived"] is False
    assert envelope["category_ids"] == ["cat_dfcf9f910000"]
    assert env.r(env.assign_path)[_NOW]["env_trans000001"] == 50.0  # NOT 100.0
    assert planning.budget_month(_NOW)["ready_to_assign"] == before


def test_nothing_assigned_this_month_is_a_no_op(env):
    env.w(env.envelopes_path, [_ENV_FOOD, _ENV_TRANSPORT])
    env.w(env.assign_path, {})
    env.set_txns([_income_now(1000.0)])

    before = planning.budget_month(_NOW)["ready_to_assign"]
    result = categories_service.delete_category(
        "cat_db5642ab0000", reassign=True, reassign_to="cat_13095ba70000"
    )
    assert result["carried_assigned"] == 0.0
    after = planning.budget_month(_NOW)
    assert after["ready_to_assign"] == before
    assert _row(after, "cat_13095ba70000")["assigned"] == 0.0


def test_on_category_deleted_reports_exactly_what_it_zeroed(env):
    """The carry reads ``zeroed`` rather than re-deriving it after the money is already gone."""
    env.w(env.envelopes_path, [_ENV_FOOD, _ENV_TRANSPORT])
    env.w(env.assign_path, {"2026-05": {"env_trans000001": 20.0}, _NOW: {"env_trans000001": 50.0}})
    env.set_txns([])
    assert envelopes_service.on_category_deleted("cat_db5642ab0000") == {
        "envelopes": 1,
        "months_zeroed": 2,
        "zeroed": {"2026-05": 20.0, _NOW: 50.0},
    }


def test_on_category_deleted_reports_nothing_when_the_envelope_survives(env):
    shared = dict(_ENV_TRANSPORT, category_ids=["cat_db5642ab0000", "cat_dfcf9f910000"])
    env.w(env.envelopes_path, [shared])
    env.w(env.assign_path, {_NOW: {"env_trans000001": 50.0}})
    env.set_txns([])
    assert envelopes_service.on_category_deleted("cat_db5642ab0000") == {
        "envelopes": 1,
        "months_zeroed": 0,
        "zeroed": {},
    }
    assert envelopes_service.on_category_deleted("cat_unlinked00") == {
        "envelopes": 0,
        "months_zeroed": 0,
        "zeroed": {},
    }


def test_add_to_assigned_routes_through_the_assign_choke(env):
    env.w(env.envelopes_path, [_ENV_FOOD])
    env.w(env.assign_path, {_NOW: {"env_food0000001": 120.0}})
    env.set_txns([])
    assert planning.add_to_assigned("env_food0000001", 30.0) == 150.0
    assert env.r(env.assign_path)[_NOW]["env_food0000001"] == 150.0
    # An explicit month wins over the default, and a non-positive delta is refused outright
    # (a negative slot is a move_money artifact, and `assign` refuses a negative result).
    assert planning.add_to_assigned("env_food0000001", 10.0, month="2026-06") == 10.0
    with pytest.raises(ValidationError):
        planning.add_to_assigned("env_food0000001", 0.0)
    with pytest.raises(ValidationError):
        planning.add_to_assigned("env_food0000001", -5.0)
    with pytest.raises(NotFoundError):
        planning.add_to_assigned("env_nope000001", 5.0)


# ── The carry may never fail the delete, move money it was not told to move, or land in a
# bot-managed pot ────────────────────────────────────────────────────────────────────────
# Three review findings against the carry above: it aborted a delete MID-WAY when the target
# was already negative (the release had committed, the category row had not), it fired on a
# bare `reassign_to` with `reassign=False`, and it could drop money inside a credit card's
# payment envelope — the one money write in the app that trusted an unenforced convention.

_ENV_CARD_PAYMENT = {
    "id": "env_card0000001",
    "name": "Visa Payment",
    "category_ids": [],
    "group_id": "others",
    "order": 2,
    "archived": False,
}


def _bot_manage_card_envelope():
    """Make ``env_card0000001`` bot-managed the way a real book does — the account FK is what
    ``bot_managed_envelope_ids``/``is_card_payment_synced`` read."""
    svc.accounts.insert(
        {
            "id": "visa",
            "name": "Visa",
            "kind": "liability",
            "type": "credit",
            "currency": "SGD",
            "payment_envelope_id": "env_card0000001",
        }
    )


def test_delete_into_a_negative_target_never_aborts_and_conserves_the_money(env):
    """BLOCKER regression: a reassign target's assigned figure can legitimately already be
    NEGATIVE (a ``move_money`` pull-back). ``assign`` refuses a negative result, so the carry
    RAISED — after ``on_category_deleted`` had already committed, leaving the category alive
    with its envelope gone and its money gone, until the identical delete was retried.
    The delete must complete, and whatever the target cannot absorb stays released to RTA."""
    env.w(env.envelopes_path, [_ENV_FOOD, _ENV_TRANSPORT])
    env.w(env.assign_path, {_NOW: {"env_trans000001": 50.0, "env_food0000001": -60.0}})
    env.set_txns([_income_now(1000.0), _expense_now("cat_db5642ab0000", 10.0)])

    before = planning.budget_month(_NOW)["ready_to_assign"]
    result = categories_service.delete_category(
        "cat_db5642ab0000", reassign=True, reassign_to="cat_13095ba70000"
    )

    assert result["ok"] and result["carried_assigned"] == 0.0
    # The delete really finished — no half-done state to retry.
    assert all(c["id"] != "cat_db5642ab0000" for c in categories_service.all_category_rows())
    # Hard-deleted for real (ADR 2026-09-26-deleting-an-envelope-deletes-it) — no more archive.
    assert envelopes_service.envelope_store.find_by_id("env_trans000001") is None
    after = planning.budget_month(_NOW)
    # Conservation: released + carried == exactly what the deleted envelope held. The target
    # is left where it was, never lifted to zero with money the envelope never had.
    released = round(after["ready_to_assign"] - before, 2)
    assert released + result["carried_assigned"] == 50.0
    assert env.r(env.assign_path)[_NOW]["env_food0000001"] == -60.0


def test_carry_lands_in_full_when_the_negative_target_can_absorb_it(env):
    """A negative target is not disqualified — only one the carry cannot lift back to zero.
    Food at -20 takes the whole 50 and lands at 30; Ready to Assign does not move."""
    env.w(env.envelopes_path, [_ENV_FOOD, _ENV_TRANSPORT])
    env.w(env.assign_path, {_NOW: {"env_trans000001": 50.0, "env_food0000001": -20.0}})
    env.set_txns([_income_now(1000.0), _expense_now("cat_db5642ab0000", 10.0)])

    before = planning.budget_month(_NOW)["ready_to_assign"]
    result = categories_service.delete_category(
        "cat_db5642ab0000", reassign=True, reassign_to="cat_13095ba70000"
    )
    assert result["carried_assigned"] == 50.0
    assert env.r(env.assign_path)[_NOW]["env_food0000001"] == 30.0
    assert planning.budget_month(_NOW)["ready_to_assign"] == before


def test_reassign_to_without_reassign_carries_nothing(env):
    """``reassign_to`` on its own is a defensive argument, not an instruction to move money
    (``delete_categories`` documents it as mattering only alongside ``reassign``). It used to
    carry anyway, returning `reassigned: 0, reassigned_to: None, carried_assigned: 50.0` — a
    response contradicting itself about the very target it named."""
    env.w(env.envelopes_path, [_ENV_FOOD, _ENV_TRANSPORT])
    env.w(env.assign_path, {_NOW: {"env_trans000001": 50.0}})
    env.set_txns([_income_now(1000.0)])  # nothing uses Transport, so no reassign is needed

    before = planning.budget_month(_NOW)["ready_to_assign"]
    result = categories_service.delete_category(
        "cat_db5642ab0000", reassign=False, reassign_to="cat_13095ba70000"
    )
    assert result["reassigned"] == 0 and result["reassigned_to"] is None
    assert result["carried_assigned"] == 0.0
    after = planning.budget_month(_NOW)
    assert after["ready_to_assign"] == round(before + 50.0, 2)  # released, exactly as before
    assert _row(after, "cat_13095ba70000")["assigned"] == 0.0


def test_a_carry_with_no_transactions_still_names_its_target(env):
    """The other half of that consistency rule: money moving is itself a reassignment, so a
    zero-transaction delete that carries reports WHERE it went. ``carried_assigned`` can never
    be non-zero beside ``reassigned_to: None``."""
    env.w(env.envelopes_path, [_ENV_FOOD, _ENV_TRANSPORT])
    env.w(env.assign_path, {_NOW: {"env_trans000001": 50.0}})
    env.set_txns([_income_now(1000.0)])

    result = categories_service.delete_category(
        "cat_db5642ab0000", reassign=True, reassign_to="cat_13095ba70000"
    )
    assert result["reassigned"] == 0 and result["in_use"] == 0
    assert result["carried_assigned"] == 50.0
    assert result["reassigned_to"] == "cat_13095ba70000"


def test_carry_never_lands_inside_a_bot_managed_envelope(env):
    """A card's payment envelope is funded by what you already charged — money the user never
    assigned there silently corrupts its coverage math and RTA. The carry used to assume such
    a row could never hold a category; nothing enforced that, so a book can already contain
    the link. Falls back to releasing, like any other "nowhere to land"."""
    linked = dict(_ENV_CARD_PAYMENT, category_ids=["cat_dfcf9f910000"])
    env.w(env.envelopes_path, [_ENV_FOOD, _ENV_TRANSPORT, linked])
    _bot_manage_card_envelope()
    env.w(env.assign_path, {_NOW: {"env_trans000001": 50.0}})
    env.set_txns([_income_now(1000.0)])

    before = planning.budget_month(_NOW)["ready_to_assign"]
    result = categories_service.delete_category(
        "cat_db5642ab0000", reassign=True, reassign_to="cat_dfcf9f910000"
    )
    assert result["carried_assigned"] == 0.0 and result["reassigned_to"] is None
    assert not env.r(env.assign_path).get(_NOW, {}).get("env_card0000001")
    assert planning.budget_month(_NOW)["ready_to_assign"] == round(before + 50.0, 2)


def test_add_category_refuses_a_bot_managed_envelope(env):
    """The root fix for the above: linking a category onto a bot-managed envelope is refused
    outright, exactly like merging, splitting or deleting one."""
    env.w(env.envelopes_path, [_ENV_FOOD, _ENV_CARD_PAYMENT])
    _bot_manage_card_envelope()
    env.set_txns([])

    with pytest.raises(ValidationError):
        envelopes_service.add_category("env_card0000001", "cat_dfcf9f910000")
    assert "cat_dfcf9f910000" not in envelopes_service.env_of()
    # An ordinary envelope is untouched by the guard.
    envelopes_service.add_category("env_food0000001", "cat_dfcf9f910000")
    assert envelopes_service.env_of()["cat_dfcf9f910000"] == "env_food0000001"


def test_add_to_assigned_skips_a_write_that_would_go_negative(env):
    """``skip_if_negative`` is the carry's escape hatch: a destination pulled below zero
    cannot take the money at all, so the caller is told ``None`` instead of being handed an
    exception it has no way to recover from. Without the flag it still raises."""
    env.w(env.envelopes_path, [_ENV_FOOD])
    env.w(env.assign_path, {_NOW: {"env_food0000001": -60.0}})
    env.set_txns([])

    assert planning.add_to_assigned("env_food0000001", 50.0, skip_if_negative=True) is None
    assert env.r(env.assign_path)[_NOW]["env_food0000001"] == -60.0
    with pytest.raises(ValidationError):
        planning.add_to_assigned("env_food0000001", 50.0)
    # Enough to cover the hole lands in full — the skip is arithmetic, not a ban on negatives.
    assert planning.add_to_assigned("env_food0000001", 70.0, skip_if_negative=True) == 10.0


def test_env_ids_never_leak_into_category_universe(env):
    _seed_migrated(env)
    cats = planning._spending_categories()
    assert not any(k.startswith("env_") for k in cats)
    assert "cat_13095ba70000" in cats


# ── P4 — the row-grouping flip: N>1 envelope math (the double-count trap) ─────────


def test_available_folds_across_envelope_members(env):
    """budget_month() must render ONE row for a merged (N>1) envelope, with assigned read
    ONCE and activity a genuine sum across members — never a per-member double-count of the
    assigned amount (P4 spec §4.1, the "double-count trap")."""
    merged = dict(
        _ENV_FOOD, id="env_merged001", category_ids=["cat_13095ba70000", "cat_dfcf9f910000"]
    )
    env.w(env.envelopes_path, [merged])
    env.w(env.assign_path, {"2026-06": {"env_merged001": 300.0}})
    env.set_txns(
        [_income(1000.0), _expense("cat_13095ba70000", 120.0), _expense("cat_dfcf9f910000", 40.0)],
    )
    table = planning.budget_month("2026-06")
    row = _row(table, "cat_13095ba70000")
    assert row["envelope_id"] == "env_merged001"
    assert row["category_ids"] == ["cat_13095ba70000", "cat_dfcf9f910000"]
    assert row["assigned"] == 300.0  # ONE read, not 300 × 2 members
    assert row["activity"] == -160.0  # folded: -120 (food) + -40 (entertainment)
    assert row["available"] == 140.0  # single assigned + Σ member activity: 300 − 160
    # "cat_dfcf9f910000" is a real member (not a phantom) — _row resolves it to the SAME row,
    # never a separate one of its own: exactly one rendered row carries this envelope_id.
    assert _row(table, "cat_dfcf9f910000") is row
    flat = [c for g in table["groups"] for c in g["categories"]]
    assert sum(1 for c in flat if c.get("envelope_id") == "env_merged001") == 1


def test_rta_unchanged_by_merge(env):
    """Merging two 1:1 envelopes must not move Ready-to-Assign — the reserved sum walks ROWS,
    never raw member categories (RFC P4 §9 BUD-ENV-MERGE)."""
    env.w(env.envelopes_path, [_ENV_FOOD, _ENV_TRANSPORT])
    env.w(env.assign_path, {"2026-06": {"env_food0000001": 300.0, "env_trans000001": 100.0}})
    env.set_txns([_income(1000.0), _expense("cat_13095ba70000", 50.0)])
    before = planning.budget_month("2026-06")["ready_to_assign"]
    envelopes_service.merge_envelope("env_trans000001", "env_food0000001")
    after = planning.budget_month("2026-06")["ready_to_assign"]
    assert after == before


# ── P4 — envelope_merge / envelope_split_category / envelope_rename ops ───────────


def test_apply_envelope_merge_conserves_money(env):
    env.w(env.envelopes_path, [_ENV_FOOD, _ENV_TRANSPORT])
    env.w(env.assign_path, {"2026-06": {"env_food0000001": 300.0, "env_trans000001": 100.0}})
    env.set_txns([_income(1000.0)])
    result = planning.apply_budget_change(
        {
            "type": "envelope_merge",
            "source_envelope_id": "env_trans000001",
            "target_envelope_id": "env_food0000001",
        }
    )
    assert result == {
        "ok": True,
        "target_envelope_id": "env_food0000001",
        "categories_moved": 1,
        "months_moved": 1,
    }
    raw = env.r(env.assign_path)["2026-06"]
    assert raw["env_food0000001"] == 400.0  # conserved: 300 + 100
    assert not raw.get("env_trans000001")
    envs = envelopes_service.envelope_store.all()
    target = next(e for e in envs if e["id"] == "env_food0000001")
    assert target["category_ids"] == ["cat_13095ba70000", "cat_db5642ab0000"]
    # Hard-deleted for real (ADR 2026-09-26-deleting-an-envelope-deletes-it) — no more archive.
    assert not any(e["id"] == "env_trans000001" for e in envs)


def test_apply_envelope_merge_validations(env):
    env.w(env.envelopes_path, [_ENV_FOOD, _ENV_TRANSPORT])
    env.w(env.assign_path, {})
    env.set_txns([])
    with pytest.raises(ValidationError):  # self-merge
        planning.apply_budget_change(
            {
                "type": "envelope_merge",
                "source_envelope_id": "env_food0000001",
                "target_envelope_id": "env_food0000001",
            }
        )
    with pytest.raises(NotFoundError):  # unknown target
        planning.apply_budget_change(
            {
                "type": "envelope_merge",
                "source_envelope_id": "env_food0000001",
                "target_envelope_id": "env_nope",
            }
        )


def test_apply_envelope_merge_refuses_commitments_synced(env, monkeypatch, tmp_path):
    """category-opaque-ids migration (decision #4): a synced commitment no longer mints a
    category — ``is_commitments_synced`` resolves via ``commitment.envelope_id`` FK, not a
    ``commitment_*`` category-key prefix. Isolate CommitmentsStore into tmp_path (this file's
    fixture does not do so by default) and stamp the FK directly."""
    from backend.db.commitments_store import CommitmentsStore

    # CommitmentsStore is DB-backed now (plan 4a task 3) — db_isolated (via env) covers it.
    commit_env = dict(_ENV_FOOD, id="env_commit01", category_ids=[])
    env.w(env.envelopes_path, [_ENV_FOOD, commit_env])
    env.w(env.assign_path, {})
    env.set_txns([])
    CommitmentsStore().create({"id": "abc123", "name": "Rent"})
    CommitmentsStore().update_commitment("abc123", {"envelope_id": "env_commit01"})
    with pytest.raises(ValidationError):
        planning.apply_budget_change(
            {
                "type": "envelope_merge",
                "source_envelope_id": "env_commit01",
                "target_envelope_id": "env_food0000001",
            }
        )


def test_apply_envelope_split_unlinks_no_new_envelope_minted(env):
    """Handoff delta #1 (unlink semantics flip, Lane BE §2): envelope_split_category no longer
    mints a new envelope — the category becomes envelope-less, and the source keeps its FULL
    assigned pool untouched (no money moves)."""
    merged = dict(
        _ENV_FOOD, id="env_merged001", category_ids=["cat_13095ba70000", "cat_dfcf9f910000"]
    )
    env.w(env.envelopes_path, [merged])
    env.w(env.assign_path, {"2026-06": {"env_merged001": 400.0}})
    env.set_txns([_income(1000.0)])
    result = planning.apply_budget_change(
        {
            "type": "envelope_split_category",
            "envelope_id": "env_merged001",
            "category_id": "cat_dfcf9f910000",
        }
    )
    assert result == {
        "ok": True,
        "category_id": "cat_dfcf9f910000",
        "envelope_id": "env_merged001",
    }
    raw = env.r(env.assign_path)["2026-06"]
    assert raw["env_merged001"] == 400.0  # source's pool untouched
    envs = envelopes_service.envelope_store.all()
    assert len(envs) == 1  # NO new envelope minted
    assert envs[0]["category_ids"] == ["cat_13095ba70000"]
    assert envelopes_service.env_of().get("cat_dfcf9f910000") is None  # genuinely unlinked


def test_apply_envelope_split_sole_member_now_legal(env):
    """Handoff delta #1: unlinking a 1:1 envelope's ONLY category is now legal (the old
    sole-member refusal is dropped — a 1:1 row gets "Remove from envelope" too). The envelope
    survives zero-category, keeping its pool; the freed category surfaces in Unbudgeted."""
    env.w(env.envelopes_path, [_ENV_FOOD])
    env.w(env.assign_path, {"2026-06": {"env_food0000001": 300.0}})
    env.set_txns([_income(1000.0), _expense("cat_13095ba70000", 45.0)])
    result = planning.apply_budget_change(
        {
            "type": "envelope_split_category",
            "envelope_id": "env_food0000001",
            "category_id": "cat_13095ba70000",
        }
    )
    assert result == {
        "ok": True,
        "category_id": "cat_13095ba70000",
        "envelope_id": "env_food0000001",
    }
    envs = envelopes_service.envelope_store.all()
    assert len(envs) == 1
    survivor = envs[0]
    assert survivor["category_ids"] == []
    assert survivor["archived"] is False
    raw = env.r(env.assign_path)["2026-06"]
    assert raw["env_food0000001"] == 300.0  # the pool stays with the (now zero-category) envelope
    table = planning.budget_month("2026-06")
    unbudgeted = next(g for g in table["groups"] if g.get("unbudgeted"))
    assert unbudgeted["categories"][0]["key"] == "cat_13095ba70000"
    assert unbudgeted["categories"][0]["activity"] == -45.0


def test_unlink_stays_unlinked_across_full_boot_resync(env):
    """Guard test (Lane BE §2): an unlink leaves NO raw category-keyed remnant — assigned
    amounts/targets are always envelope-keyed (docs/adr/2026-07-05-envelope-id-native.md) — so
    the boot-time seed (``ensure_envelopes_migrated``, a no-op on any book with envelopes
    already present) never re-mints an envelope for the freshly-unlinked category."""
    merged = dict(
        _ENV_FOOD, id="env_merged001", category_ids=["cat_13095ba70000", "cat_dfcf9f910000"]
    )
    env.w(env.envelopes_path, [merged])
    env.w(env.assign_path, {"2026-06": {"env_merged001": 400.0}})
    env.set_txns([_income(1000.0)])
    envelopes_service.split_category("env_merged001", "cat_dfcf9f910000")
    assert envelopes_service.env_of().get("cat_dfcf9f910000") is None

    envelopes_service.ensure_envelopes_migrated()

    assert envelopes_service.env_of().get("cat_dfcf9f910000") is None
    envs = envelopes_service.envelope_store.all()
    assert len(envs) == 1  # still no envelope minted for it


def test_apply_envelope_split_validations(env):
    env.w(env.envelopes_path, [_ENV_FOOD])
    env.w(env.assign_path, {})
    env.set_txns([])
    with pytest.raises(ValidationError):  # not a member of this envelope
        planning.apply_budget_change(
            {
                "type": "envelope_split_category",
                "envelope_id": "env_food0000001",
                "category_id": "cat_db5642ab0000",
            }
        )


# ── row-note lifecycle (docs/adr/2026-07-05-envelope-id-native.md) ────────────────
# A row's note lives at its OWN ENVELOPE ID — never a category key. Row identity is
# permanently pinned to the envelope id, so a note only ever DROPS at an envelope's death
# (merge source archives, delete) — it never needs to "follow" anywhere, since split/rename
# never change which envelope id a row is.


def test_split_category_note_survives_regardless_of_which_member_unlinks(env):
    """Splitting a member off a merged envelope never touches its note — the envelope's own
    id (the note's only key) doesn't change, whether the detached member was the first
    (``category_ids[0]``) or a later one."""
    merged = dict(
        _ENV_FOOD, id="env_merged001", category_ids=["cat_13095ba70000", "cat_dfcf9f910000"]
    )
    env.w(env.envelopes_path, [merged])
    env.w(env.assign_path, {"2026-06": {"env_merged001": 400.0}})
    env.set_txns([])
    planning.write_category_note("env_merged001", "buy in bulk")
    assert planning.read_category_notes() == {"env_merged001": "buy in bulk"}

    envelopes_service.split_category("env_merged001", "cat_13095ba70000")  # unlink the first member

    assert planning.read_category_notes() == {"env_merged001": "buy in bulk"}


def test_split_category_unlink_non_primary_note_untouched(env):
    merged = dict(
        _ENV_FOOD, id="env_merged001", category_ids=["cat_13095ba70000", "cat_dfcf9f910000"]
    )
    env.w(env.envelopes_path, [merged])
    env.w(env.assign_path, {"2026-06": {"env_merged001": 400.0}})
    env.set_txns([])
    planning.write_category_note("env_merged001", "buy in bulk")

    envelopes_service.split_category("env_merged001", "cat_dfcf9f910000")  # non-primary unlink

    assert planning.read_category_notes() == {"env_merged001": "buy in bulk"}  # unchanged


def test_merge_envelope_source_note_dies_target_note_untouched(env):
    env.w(env.envelopes_path, [_ENV_FOOD, _ENV_TRANSPORT])
    env.w(env.assign_path, {"2026-06": {"env_food0000001": 300.0, "env_trans000001": 100.0}})
    env.set_txns([])
    planning.write_category_note("env_food0000001", "keep this")
    planning.write_category_note("env_trans000001", "drop this")

    envelopes_service.merge_envelope("env_trans000001", "env_food0000001")

    notes = planning.read_category_notes()
    assert notes.get("env_food0000001") == "keep this"  # target's own note is UNTOUCHED
    assert notes.get("env_trans000001") is None  # source's note dies with the row


def test_delete_envelope_note_gone(env):
    env.w(env.envelopes_path, [_ENV_FOOD])
    env.w(env.assign_path, {"2026-06": {"env_food0000001": 300.0}})
    env.set_txns([])
    planning.write_category_note("env_food0000001", "grocery money")

    envelopes_service.delete_envelope("env_food0000001")

    assert planning.read_category_notes().get("env_food0000001") is None


def test_stray_non_envelope_key_note_never_treated_as_a_remnant(env):
    """A note stored under a key that ISN'T any live envelope's id (a stray/legacy entry —
    e.g. a bare category key from before the envelope-id-native cutover) must never be
    touched by any envelope op — every op explicitly addresses its OWN referenced envelope
    id, nothing else, so a stray key just survives untouched."""
    merged = dict(
        _ENV_FOOD, id="env_merged001", category_ids=["cat_13095ba70000", "cat_dfcf9f910000"]
    )
    env.w(env.envelopes_path, [merged])
    env.w(env.assign_path, {"2026-06": {"env_merged001": 400.0}})
    env.set_txns([])
    budgets_service._write_profile_key("category_notes", {"cat_dfcf9f910000": "stray ghost note"})

    envelopes_service.split_category("env_merged001", "cat_dfcf9f910000")  # non-primary unlink

    assert planning.read_category_notes() == {"cat_dfcf9f910000": "stray ghost note"}


# ── ENV-RES-08 fix (2026-07-04) — envelope-first row label ────────────────────────


def test_row_label_shows_independently_set_envelope_name_on_a_1to1_row(env):
    """A 1:1 row used to always show the category's own label, silently discarding a name
    the user typed over a single pick — now the envelope's OWN name wins whenever it's
    non-empty (fix, ENV-RES-08)."""
    named = dict(
        _ENV_FOOD, name="Household"
    )  # independently named, category_ids=["cat_13095ba70000"]
    env.w(env.envelopes_path, [named])
    env.w(env.assign_path, {})
    env.set_txns([])
    row = _row(planning.budget_month("2026-06"), "cat_13095ba70000")
    assert row["label"] == "Household"


def test_row_label_falls_back_to_category_label_when_envelope_name_matches(env):
    """The 1:1 byte-identity book keeps name==label (auto-minted names ARE the category
    label), so this is a structural no-op there."""
    env.w(env.envelopes_path, [_ENV_FOOD])  # name="Food", matches _category_label fallback
    env.w(env.assign_path, {})
    env.set_txns([])
    row = _row(planning.budget_month("2026-06"), "cat_13095ba70000")
    assert row["label"] == "Food"


def test_row_label_zero_category_envelope_falls_back_to_key_when_nameless(env):
    """A zero-category envelope always HAS a name in practice (create_envelope requires one)
    — but the fallback chain (name → primary category label → key) must not crash if a
    legacy/hand-edited row somehow has neither."""
    nameless = {
        "id": "env_bare0000001",
        "name": "",
        "category_ids": [],
        "group_id": "others",
        "order": 0,
        "archived": False,
    }
    env.w(env.envelopes_path, [nameless])
    env.w(env.assign_path, {})
    env.set_txns([])
    row = _row(planning.budget_month("2026-06"), "env_bare0000001")
    assert row["label"] == "env_bare0000001"


def test_apply_envelope_rename(env):
    env.w(env.envelopes_path, [_ENV_FOOD])
    env.w(env.assign_path, {})
    env.set_txns([])
    result = planning.apply_budget_change(
        {"type": "envelope_rename", "envelope_id": "env_food0000001", "name": "Groceries & Dining"}
    )
    assert result["ok"] is True
    envs = envelopes_service.envelope_store.all()
    assert next(e for e in envs if e["id"] == "env_food0000001")["name"] == "Groceries & Dining"


def test_apply_envelope_rename_blank_refused(env):
    env.w(env.envelopes_path, [_ENV_FOOD])
    env.w(env.assign_path, {})
    env.set_txns([])
    with pytest.raises(ValidationError):
        planning.apply_budget_change(
            {"type": "envelope_rename", "envelope_id": "env_food0000001", "name": "  "}
        )


# ── P4 hardening (code review) — BLOCKER 1: auto-assign is envelope-row-aware ──────


def test_auto_assign_underfunded_folds_envelope_once(env):
    """BLOCKER 1 repro (superseded shape): a merged envelope used to be able to carry TWO
    independent per-member targets both firing their own auto-assign step against the SAME
    stored slot. Targets are envelope-id native now (docs/adr/2026-07-05-envelope-id-native.md)
    — a row has exactly ONE target, full stop, so auto_assign_preview structurally emits ONE
    step per ROW, keyed by ``envelope_id``."""
    merged = dict(
        _ENV_FOOD, id="env_merged001", category_ids=["cat_13095ba70000", "cat_dfcf9f910000"]
    )
    env.w(env.envelopes_path, [merged])
    env.w(env.assign_path, {})
    env.set_txns([_income(1000.0)])
    planning.write_category_targets({"env_merged001": {"type": "monthly", "amount": 200.0}})
    preview = planning.auto_assign_preview("2026-06", "underfunded")
    assert len(preview["steps"]) == 1
    assert preview["steps"][0]["envelope_id"] == "env_merged001"
    assert preview["steps"][0]["new_assigned"] == 200.0
    assert preview["total_delta"] == 200.0

    result = planning.apply_budget_change(
        {"type": "auto_assign", "month": "2026-06", "mode": "underfunded"}
    )
    assert result == {"applied": 1, "total_delta": 200.0}
    raw = env.r(env.assign_path)["2026-06"]
    assert raw["env_merged001"] == 200.0


def test_auto_assign_spent_last_month_folds_envelope_activity(env):
    """spent_last_month must fold NET activity across every member of the row for the prior
    month (matching the row's own Activity column), not just the primary's own spend."""
    merged = dict(
        _ENV_FOOD, id="env_merged001", category_ids=["cat_13095ba70000", "cat_dfcf9f910000"]
    )
    env.w(env.envelopes_path, [merged])
    env.w(env.assign_path, {})
    env.set_txns(
        [
            _income(1000.0),
            {**_expense("cat_13095ba70000", 120.0), "date": "2026-05-10"},
            {**_expense("cat_dfcf9f910000", 40.0), "date": "2026-05-15"},
        ],
    )
    preview = planning.auto_assign_preview("2026-06", "spent_last_month")
    assert len(preview["steps"]) == 1
    assert preview["steps"][0]["envelope_id"] == "env_merged001"
    assert preview["steps"][0]["new_assigned"] == 160.0  # folded: 120 (food) + 40 (entertainment)


def test_auto_assign_reset_one_step_per_envelope_total_delta_truthful(env):
    """The other modes double-counted total_delta the same way (one step per member, all
    targeting the same slot). reset_assigned on an N>1 envelope must produce exactly ONE
    step whose delta is the envelope's true (single) assigned amount, not doubled."""
    merged = dict(
        _ENV_FOOD, id="env_merged001", category_ids=["cat_13095ba70000", "cat_dfcf9f910000"]
    )
    env.w(env.envelopes_path, [merged])
    env.w(env.assign_path, {"2026-06": {"env_merged001": 300.0}})
    env.set_txns([_income(1000.0)])
    preview = planning.auto_assign_preview("2026-06", "reset_assigned")
    assert len(preview["steps"]) == 1
    assert preview["total_delta"] == -300.0  # not -600 (double-counted across 2 members)


# ── P4 hardening (code review) — BLOCKER 2: move_money refuses a same-envelope pair ────


def test_move_money_within_same_envelope_refused(env):
    """BLOCKER 2 repro: dining and coffee folded into ONE envelope resolve to the SAME
    assignment slot; move_money read-before-writes both ends, so moving 50 dining→coffee
    used to net +50 out of nowhere (200 -> 250, no transaction). Must be refused outright."""
    merged = dict(_ENV_FOOD, id="env_merged001", category_ids=["dining", "coffee"])
    env.w(env.envelopes_path, [merged])
    env.w(env.assign_path, {"2026-06": {"env_merged001": 200.0}})
    env.set_txns([_income(1000.0)])
    with pytest.raises(ValidationError):
        planning.apply_budget_change(
            {
                "type": "move_money",
                "month": "2026-06",
                "from_envelope_id": "env_merged001",
                "to_envelope_id": "env_merged001",
                "amount": 50.0,
            }
        )
    raw = env.r(env.assign_path)["2026-06"]
    assert raw["env_merged001"] == 200.0  # untouched — no money fabricated


# ── P4 hardening (code review) — SHOULD-FIX 3: group placement follows the envelope ────


def test_grouped_rows_survives_cross_group_merge_and_unlink_of_primary(env):
    """SHOULD-FIX 3 repro (merge half unchanged): merging food (Group A) into transport
    (Group B) must place the surviving row by envelope.group_id (Group B), never a stale
    category_groups_store lookup. Handoff delta #1 flips the split half: unlinking
    "cat_db5642ab0000" (the merged primary) makes it envelope-less — NO new envelope, no row of its
    own — while the survivor keeps ["cat_13095ba70000"] + the whole $400 pool, still in Group B."""
    grp_a = {
        "id": "grpA",
        "name": "Group A",
        "collapsed": False,
        "order": 0,
    }
    grp_b = {
        "id": "grpB",
        "name": "Group B",
        "collapsed": False,
        "order": 1,
    }
    env.w(env.groups_path, [grp_a, grp_b])
    env_food = dict(_ENV_FOOD, id="env_food0000001", group_id="grpA")
    env_transport = dict(_ENV_TRANSPORT, id="env_trans000001", group_id="grpB")
    env.w(env.envelopes_path, [env_food, env_transport])
    env.w(env.assign_path, {"2026-06": {"env_food0000001": 400.0}})
    env.set_txns([_income(1000.0)])

    # Merge food (Group A) into transport (Group B) — transport stays primary (its category
    # key sorts first, RFC merge order), its group_id untouched (still Group B).
    envelopes_service.merge_envelope("env_food0000001", "env_trans000001")
    merged_table = planning.budget_month("2026-06")
    merged_groups = {g["name"]: g for g in merged_table["groups"]}
    merged_row = _row(merged_table, "cat_db5642ab0000")
    assert merged_row["available"] == 400.0
    assert merged_row["envelope_id"] == "env_trans000001"
    assert any(c["key"] == "env_trans000001" for c in merged_groups["Group B"]["categories"])
    assert not any(
        c["key"] == "env_trans000001"
        for c in merged_groups.get("Group A", {}).get("categories", [])
    )

    # Unlink "cat_db5642ab0000" (the merged primary) — envelope-less, no envelope minted; the
    # survivor keeps ["cat_13095ba70000"] + the full pool, still placed in Group B (its row key now
    # flips to "cat_13095ba70000", the sole remaining member).
    envelopes_service.split_category("env_trans000001", "cat_db5642ab0000")
    envs = envelopes_service.envelope_store.all()
    # 1 survivor: the merge hard-deleted "env_food0000001" for real (ADR
    # 2026-09-26-deleting-an-envelope-deletes-it), and the split mints no new envelope either.
    assert len(envs) == 1
    survivor = next(e for e in envs if e["id"] == "env_trans000001")
    assert survivor["category_ids"] == ["cat_13095ba70000"]

    table = planning.budget_month("2026-06")
    groups_by_name = {g["name"]: g for g in table["groups"]}
    food_row = _row(table, "cat_13095ba70000")
    assert food_row["available"] == 400.0  # pool untouched by the unlink
    assert food_row["envelope_id"] == "env_trans000001"
    assert any(c["key"] == "env_trans000001" for c in groups_by_name["Group B"]["categories"])
    with pytest.raises(AssertionError):
        _row(table, "cat_db5642ab0000")  # no row of its own anymore — genuinely unlinked now


def test_move_category_keeps_11_envelope_group_in_sync(env):
    """A 1:1 (envelope-backed) category moved via move_category must relocate its row —
    otherwise, now that placement follows envelope.group_id, an already-migrated category's
    'Move to group' would silently stop working."""
    grp_a = {
        "id": "grpA",
        "name": "Group A",
        "collapsed": False,
        "order": 0,
    }
    grp_b = {"id": "grpB", "name": "Group B", "collapsed": False, "order": 1}
    env.w(env.groups_path, [grp_a, grp_b])
    env.w(env.envelopes_path, [dict(_ENV_FOOD, group_id="grpA")])
    env.w(env.assign_path, {"2026-06": {"env_food0000001": 100.0}})
    env.set_txns([_income(1000.0)])

    planning.apply_budget_change(
        {"type": "move_category", "category": "env_food0000001", "group_id": "grpB"}
    )

    table = planning.budget_month("2026-06")
    groups_by_name = {g["name"]: g for g in table["groups"]}
    assert any(c["key"] == "env_food0000001" for c in groups_by_name["Group B"]["categories"])
    assert not any(c["key"] == "env_food0000001" for c in groups_by_name["Group A"]["categories"])
    envs = envelopes_service.envelope_store.all()
    assert next(e for e in envs if e["id"] == "env_food0000001")["group_id"] == "grpB"


# ── P4 hardening (code review) — NITs: the two missing guard tests ────────────────


def test_apply_envelope_split_refuses_commitments_synced(env, monkeypatch, tmp_path):
    """category-opaque-ids migration (decision #4): see the merge-guard test above for the FK
    rationale."""
    from backend.db.commitments_store import CommitmentsStore

    # CommitmentsStore is DB-backed now (plan 4a task 3) — db_isolated (via env) covers it.
    commit_env = dict(_ENV_FOOD, id="env_commit01", category_ids=["cat_13095ba70000"])
    env.w(env.envelopes_path, [commit_env])
    env.w(env.assign_path, {})
    env.set_txns([])
    CommitmentsStore().create({"id": "abc123", "name": "Rent"})
    CommitmentsStore().update_commitment("abc123", {"envelope_id": "env_commit01"})
    with pytest.raises(ValidationError):
        planning.apply_budget_change(
            {
                "type": "envelope_split_category",
                "envelope_id": "env_commit01",
                "category_id": "cat_13095ba70000",
            }
        )


def test_apply_envelope_merge_refuses_archived_either_side(env):
    archived_env = dict(_ENV_FOOD, id="env_archived1", archived=True)
    env.w(env.envelopes_path, [archived_env, _ENV_TRANSPORT])
    env.w(env.assign_path, {})
    env.set_txns([])
    with pytest.raises(ValidationError):  # archived SOURCE
        planning.apply_budget_change(
            {
                "type": "envelope_merge",
                "source_envelope_id": "env_archived1",
                "target_envelope_id": "env_trans000001",
            }
        )
    with pytest.raises(ValidationError):  # archived TARGET
        planning.apply_budget_change(
            {
                "type": "envelope_merge",
                "source_envelope_id": "env_trans000001",
                "target_envelope_id": "env_archived1",
            }
        )


# ── Backend fix wave (docs/adr/2026-07-02-balance-based-rta.md) — BLOCKERS 1-3 ────


def test_rename_merge_from_unmapped_category_is_a_money_noop(env):
    """Superseded BLOCKER 1: merging an UNMAPPED category (a fresh one — create_category mints
    no envelope, RFC full-split §Backend 2) INTO a mapped one used to crash, then later (pre
    envelope-id-native) folded its stray remnant money in. Money is envelope-id native now
    (docs/adr/2026-07-05-envelope-id-native.md) — the legacy-remnant auto-heal machinery is
    gone, so ``on_category_renamed`` is a hard no-op whenever the OLD key was never linked:
    nothing crashes, but nothing folds either — any stray raw money under the old bare key
    (a legacy remnant / bot-managed write) is simply left exactly where it was."""
    _seed_migrated(env, assigned_food=300.0)
    created = categories_service.create_category("Startup", "expense")
    startup_id = created["id"]
    assert envelopes_service.env_of().get(startup_id) is None  # genuinely unmapped, no auto-mint
    raw = env.r(env.assign_path)
    raw["2026-06"][startup_id] = 50.0
    env.w(env.assign_path, raw)

    categories_service.rename_category(startup_id, merge_into="cat_13095ba70000")  # must not raise

    raw = env.r(env.assign_path)["2026-06"]
    assert raw["env_food0000001"] == 300.0  # untouched — nothing to fold FROM an unmapped source
    assert raw.get(startup_id) == 50.0  # the stray remnant is left in place, not vanished
    assert _row(planning.budget_month("2026-06"), "cat_13095ba70000")["assigned"] == 300.0


def test_rename_merge_into_unmapped_category_relabels_in_place_no_fold(env):
    """Superseded BLOCKER 2 (the mirror direction): merging a MAPPED category INTO an unmapped
    one has no second envelope to fold into, so it takes the plain-rename branch — the SAME
    envelope just swaps its ``category_ids`` member label, money untouched. A stray raw
    remnant already sitting under the destination key is left alone, not folded in (the
    legacy-remnant auto-heal machinery is gone, docs/adr/2026-07-05-envelope-id-native.md)."""
    created = categories_service.create_category("Dining", "expense")  # unmapped by construction
    dining_id = created["id"]
    _seed_migrated(env, assigned_food=300.0)
    assert envelopes_service.env_of().get(dining_id) is None
    raw = env.r(env.assign_path)
    raw.setdefault("2026-06", {})[dining_id] = 20.0
    env.w(env.assign_path, raw)
    raw = env.r(env.assign_path)["2026-06"]
    assert raw[dining_id] == 20.0  # unmapped, cat-keyed — sanity
    assert raw["env_food0000001"] == 300.0

    categories_service.rename_category("cat_13095ba70000", merge_into=dining_id)

    raw = env.r(env.assign_path)["2026-06"]
    assert raw["env_food0000001"] == 300.0  # unchanged — nothing folded in
    assert raw.get(dining_id) == 20.0  # pre-existing stray remnant left exactly where it was
    row = _row(planning.budget_month("2026-06"), dining_id)
    assert row["assigned"] == 300.0
    envs = envelopes_service.envelope_store.all()
    food_env = next(e for e in envs if e["id"] == "env_food0000001")
    assert food_env["category_ids"] == [dining_id]  # relabeled in place, same envelope


def test_shouldfix3_merge_of_n_gt_1_member_only_detaches_no_pool_money_moves(env):
    """SHOULD-FIX 3 repro: merging "cat_13095ba70000" (one of TWO members of a merged envelope) into
    "cat_db5642ab0000" used to move the WHOLE pool's assigned into transport and archive the
    envelope, stranding "cat_dfcf9f910000" penniless. Only "cat_13095ba70000" should detach (split
    semantics — no pool money moves); "cat_dfcf9f910000" keeps env_merged's full balance, and
    "cat_db5642ab0000" gains nothing (food carried $0 into the merge)."""
    merged = dict(
        _ENV_FOOD, id="env_merged001", category_ids=["cat_13095ba70000", "cat_dfcf9f910000"]
    )
    env.w(env.envelopes_path, [merged, _ENV_TRANSPORT])
    env.w(env.assign_path, {"2026-06": {"env_merged001": 300.0, "env_trans000001": 100.0}})
    env.set_txns([_income(1000.0)])

    categories_service.rename_category("cat_13095ba70000", merge_into="cat_db5642ab0000")

    raw = env.r(env.assign_path)["2026-06"]
    assert raw["env_merged001"] == 300.0  # untouched — stays with entertainment
    assert raw["env_trans000001"] == 100.0  # untouched — food carried $0 into the merge

    envs = {e["id"]: e for e in envelopes_service.envelope_store.all()}
    assert envs["env_merged001"]["category_ids"] == ["cat_dfcf9f910000"]
    assert envs["env_merged001"]["archived"] is False  # 2+ members before detach — still alive
    assert envs["env_trans000001"]["category_ids"] == [
        "cat_db5642ab0000"
    ]  # "cat_13095ba70000" absorbed in place

    table = planning.budget_month("2026-06")
    assert _row(table, "cat_dfcf9f910000")["assigned"] == 300.0
    assert _row(table, "cat_db5642ab0000")["assigned"] == 100.0


# ── Full split (RFC envelope-category-full-split.md §Backend 2) — create_category mints
#    NO envelope, on ANY book — supersedes SHOULD-FIX 8's "mint on an already-migrated book"
#    behavior, which this RFC deliberately removes (envelope ⊥ category creation).


def test_create_category_on_unmigrated_book_stays_unlinked(env):
    env.w(env.envelopes_path, [])
    env.w(env.assign_path, {})
    env.set_txns([])
    created = categories_service.create_category("Startup", "expense")
    assert envelopes_service.env_of().get(created["id"]) is None


def test_create_category_on_migrated_book_stays_unlinked(env):
    """The FORMER SHOULD-FIX 8 behavior (mint a 1:1 envelope on an already-migrated book) is
    gone — the full split's whole point is that envelope and category creation are
    independent (RFC full-split §Backend 2): even on a migrated book, a fresh category gets
    NO envelope and therefore no budget row."""
    _seed_migrated(env)
    result = categories_service.create_category("Startup", "expense")
    assert envelopes_service.env_of().get(result["id"]) is None
    assert result["label"] == "Startup"
    with pytest.raises(AssertionError):  # no row at all, not even a zero one
        _row(planning.budget_month("2026-06"), result["id"])


def test_shouldfix8_income_category_never_mints_envelope(env):
    """Envelopes are a spending-axis concept — an income-kind category must never get one,
    even on a fully migrated book (it would be an orphan no row ever reads)."""
    _seed_migrated(env)
    created = categories_service.create_category("Freelance", "income")
    assert envelopes_service.env_of().get(created["id"]) is None


# ── Backend fix wave — BLOCKER 4: reorder_categories on a migrated book ───────────


def test_blocker4_reorder_categories_actually_reorders_migrated_envelope_rows(env):
    """BLOCKER 4 repro: on a migrated book, budget_month renders envelope rows ordered by
    envelope.order — reorder_categories used to only persist groups_store member order
    (which NOTHING reads for envelope-backed rows), a silent no-op."""
    grp_a = {"id": "grpA", "name": "Group A", "collapsed": False, "order": 0}
    env.w(env.groups_path, [grp_a])
    env_food = dict(_ENV_FOOD, group_id="grpA", order=0)
    env_transport = dict(_ENV_TRANSPORT, group_id="grpA", order=1)
    env.w(env.envelopes_path, [env_food, env_transport])
    env.w(env.assign_path, {})
    env.set_txns([])

    table = planning.budget_month("2026-06")
    grp = next(g for g in table["groups"] if g["id"] == "grpA")
    assert [c["key"] for c in grp["categories"]] == ["env_food0000001", "env_trans000001"]

    result = planning.apply_budget_change(
        {
            "type": "reorder_categories",
            "group_id": "grpA",
            "categories": ["env_trans000001", "env_food0000001"],
        }
    )
    assert result["ok"] is True

    table = planning.budget_month("2026-06")
    grp = next(g for g in table["groups"] if g["id"] == "grpA")
    # actually reordered
    assert [c["key"] for c in grp["categories"]] == ["env_trans000001", "env_food0000001"]
    envs = {e["id"]: e for e in envelopes_service.envelope_store.all()}
    assert envs["env_trans000001"]["order"] < envs["env_food0000001"]["order"]


def test_blocker4_reorder_with_n_gt_1_row_does_not_422(env):
    """A group containing an N>1 (merged) row used to ALWAYS 422 — the stale groups_store
    member list still carries every original member ("cat_13095ba70000" AND "cat_dfcf9f910000") while only
    "cat_13095ba70000" (the primary) ever renders as its own row, so an honest client request naming just
    the rendered rows never matched ``sorted(group.categories)``."""
    grp_a = {
        "id": "grpA",
        "name": "Group A",
        "collapsed": False,
        "order": 0,
    }
    env.w(env.groups_path, [grp_a])
    merged = dict(
        _ENV_FOOD, id="env_merged001", category_ids=["cat_13095ba70000", "cat_dfcf9f910000"]
    )
    merged["group_id"] = "grpA"
    transport = dict(_ENV_TRANSPORT, group_id="grpA", order=1)
    env.w(env.envelopes_path, [merged, transport])
    env.w(env.assign_path, {})
    env.set_txns([])

    # The client only ever sees + sends the two RENDERED rows: "env_merged001" (the merged
    # primary, "cat_13095ba70000") and "env_trans000001" ("cat_db5642ab0000") — never "cat_dfcf9f910000".
    result = planning.apply_budget_change(
        {
            "type": "reorder_categories",
            "group_id": "grpA",
            "categories": ["env_trans000001", "env_merged001"],
        }
    )
    assert result["ok"] is True

    table = planning.budget_month("2026-06")
    grp = next(g for g in table["groups"] if g["id"] == "grpA")
    assert [c["key"] for c in grp["categories"]] == ["env_trans000001", "env_merged001"]


# ── Backend fix wave — SHOULD-FIX 5: move_category N>1 refusal + index sync ───────


def test_shouldfix5_move_category_refuses_non_primary_n_gt_1_member(env):
    """``move_category``'s ``category`` field is an envelope id now (docs/adr/
    2026-07-05-envelope-id-native.md) — there's no more "resolve a member key to its row"
    indirection, so a merged (N>1) envelope simply can't move via move_category at all
    (split it out first); the refusal names the merged envelope, not a specific member."""
    merged = dict(
        _ENV_FOOD, id="env_merged001", category_ids=["cat_13095ba70000", "cat_dfcf9f910000"]
    )
    env.w(env.envelopes_path, [merged])
    grp_b = {"id": "grpB", "name": "Group B", "collapsed": False, "order": 1}
    env.w(env.groups_path, [grp_b])
    env.w(env.assign_path, {})
    env.set_txns([])

    with pytest.raises(ValidationError, match="merged envelope"):
        planning.apply_budget_change(
            {"type": "move_category", "category": "env_merged001", "group_id": "grpB"}
        )
    # Nothing moved — the envelope is untouched.
    envs = envelopes_service.envelope_store.all()
    assert next(e for e in envs if e["id"] == "env_merged001")["group_id"] == "others"


def test_shouldfix5_move_category_index_positions_among_destination_siblings(env):
    """``index`` used to be completely ignored for envelope-backed rows (always appended
    dead-last via ``order=len(all envelopes)``). It must now actually land the moved row at
    the requested position among the destination group's OTHER envelope rows, renumbering
    siblings so nothing collides."""
    grp_a = {"id": "grpA", "name": "Group A", "collapsed": False, "order": 0}
    grp_b = {"id": "grpB", "name": "Group B", "collapsed": False, "order": 1}
    env.w(env.groups_path, [grp_a, grp_b])
    food = dict(_ENV_FOOD, group_id="grpA", order=0)
    transport = dict(_ENV_TRANSPORT, group_id="grpB", order=0)
    coffee = {
        "id": "env_health0000001",
        "name": "Health",
        "category_ids": ["cat_6ea706940000"],
        "group_id": "grpB",
        "order": 1,
        "archived": False,
    }
    env.w(env.envelopes_path, [food, transport, coffee])
    env.w(env.assign_path, {})
    env.set_txns([])

    planning.apply_budget_change(
        {"type": "move_category", "category": "env_food0000001", "group_id": "grpB", "index": 1}
    )

    table = planning.budget_month("2026-06")
    grp = next(g for g in table["groups"] if g["id"] == "grpB")
    assert [c["key"] for c in grp["categories"]] == [
        "env_trans000001",
        "env_food0000001",
        "env_health0000001",
    ]


# ── EXTRA A (regression, 2026-07-04) — move_category refuses Commitments ──


def test_move_category_refuses_commitments_synced_envelope(env, monkeypatch, tmp_path):
    """A Lucius reorg once moved commitment envelopes out of the pinned Commitments group
    because move_category was the one write path that didn't share the
    ``is_commitments_synced`` guard merge/split/delete already enforce. Refused now, same as
    the others; ``sync_commitment_envelopes`` self-heals an already-drifted book.
    category-opaque-ids migration (decision #4): the guard now resolves via
    ``commitment.envelope_id`` FK — isolate CommitmentsStore into tmp_path and stamp it."""
    from backend.db.commitments_store import CommitmentsStore

    # CommitmentsStore is DB-backed now (plan 4a task 3) — db_isolated (via env) covers it.
    commitments_env = dict(
        _ENV_FOOD,
        id="env_commit0000001",
        category_ids=[],
        group_id="grp_commitments",
    )
    env.w(env.envelopes_path, [commitments_env])
    grp_b = {"id": "grpB", "name": "Group B", "collapsed": False, "order": 1}
    env.w(env.groups_path, [grp_b])
    env.w(env.assign_path, {})
    env.set_txns([])
    CommitmentsStore().create({"id": "car", "name": "Car loan"})
    CommitmentsStore().update_commitment("car", {"envelope_id": "env_commit0000001"})

    with pytest.raises(ValidationError, match="Commitments envelopes cannot be moved"):
        planning.apply_budget_change(
            {"type": "move_category", "category": "env_commit0000001", "group_id": "grpB"}
        )
    envs = envelopes_service.envelope_store.all()
    assert next(e for e in envs if e["id"] == "env_commit0000001")["group_id"] == "grp_commitments"


def _two_groups_and_a_plain_envelope(env, dest: dict) -> None:
    plain = dict(_ENV_FOOD, id="env_plain0000001", group_id="grpA")
    env.w(env.envelopes_path, [plain])
    grp_a = {"id": "grpA", "name": "Group A", "collapsed": False, "order": 0}
    env.w(env.groups_path, [grp_a, dest])
    env.w(env.assign_path, {})
    env.set_txns([])


def test_move_category_refuses_a_synced_destination(env):
    """The destination half of the guard above: Commitments and Credit Card Payments are kept by
    the app, so nothing may be moved INTO them either — the table locks their rows, so an
    envelope dropped in could never come back out. 50/30/20's editor offered them by name."""
    cc = {"id": "grpCC", "name": "Credit Card Payments", "order": 1}
    _two_groups_and_a_plain_envelope(env, cc)
    with pytest.raises(ValidationError, match="kept in sync by the app"):
        planning.apply_budget_change(
            {"type": "move_category", "category": "env_plain0000001", "group_id": "grpCC"}
        )
    envs = envelopes_service.envelope_store.all()
    assert next(e for e in envs if e["id"] == "env_plain0000001")["group_id"] == "grpA"


def test_move_category_refuses_an_income_category(env):
    """Income has no envelope and belongs to no group — there is nothing to move."""
    from backend.db.category_store import CategoryStore

    CategoryStore().insert({"id": "cat_pay0000001", "label": "Pay", "kind": "income"})
    _two_groups_and_a_plain_envelope(env, {"id": "grpB", "name": "Group B", "order": 1})
    with pytest.raises(ValidationError, match="income categories have no envelope"):
        planning.apply_budget_change(
            {"type": "move_category", "category": "cat_pay0000001", "group_id": "grpB"}
        )


def test_list_groups_flags_only_the_groups_the_app_keeps(env):
    env.w(
        env.groups_path,
        [
            {"id": "grpA", "name": "Group A", "order": 0},
            {"id": "grpC", "name": "Commitments", "order": 1},
        ],
    )
    groups = planning.list_groups()
    assert {g["id"]: g["synced"] for g in groups} == {"grpA": False, "grpC": True}
    assert all("income" not in g and "categories" not in g for g in groups)


# ── Backend fix wave — SHOULD-FIX 6: set_target resolves to the row's PRIMARY key ─


def test_shouldfix6_set_target_via_envelope_id_lands_on_the_row_and_takes_effect(env):
    """SHOULD-FIX 6 (superseded shape): a non-primary member's key used to silently write a
    ghost target ``budget_month`` never read. Targets are envelope-id native now (docs/adr/
    2026-07-05-envelope-id-native.md) — there's no more member-key indirection to resolve;
    the caller passes the envelope's OWN id directly, and the write lands there, full stop,
    regardless of which categories are members."""
    merged = dict(
        _ENV_FOOD, id="env_merged001", category_ids=["cat_13095ba70000", "cat_dfcf9f910000"]
    )
    env.w(env.envelopes_path, [merged])
    env.w(env.assign_path, {})
    env.set_txns([_income(1000.0)])

    result = planning.apply_budget_change(
        {
            "type": "set_target",
            "envelope_id": "env_merged001",
            "target_type": "monthly",
            "amount": 200.0,
        }
    )
    assert result["envelope_id"] == "env_merged001"

    targets = planning.read_category_targets()
    assert "env_merged001" in targets  # ENVELOPE-keyed storage
    assert "cat_13095ba70000" not in targets and "cat_dfcf9f910000" not in targets

    row = _row(planning.budget_month("2026-06"), "cat_13095ba70000")
    assert row["target"] is not None
    assert row["status"] == "underfunded"
    assert row["needed_this_month"] == pytest.approx(200.0)


# ── write_category_note is envelope-id native ─────────────────────────────────────


def test_write_category_note_lands_on_the_envelope_id(env):
    merged = dict(
        _ENV_FOOD, id="env_merged001", category_ids=["cat_13095ba70000", "cat_dfcf9f910000"]
    )
    env.w(env.envelopes_path, [merged])
    env.w(env.assign_path, {})
    env.set_txns([])

    planning.write_category_note("env_merged001", "A note on the row")

    notes = planning.read_category_notes()
    assert notes.get("env_merged001") == "A note on the row"
    assert "cat_13095ba70000" not in notes and "cat_dfcf9f910000" not in notes


# ── Full split (RFC envelope-category-full-split.md) — envelope_create ────────────


def test_create_envelope_service_zero_categories(env):
    env.w(env.envelopes_path, [])
    env.w(env.assign_path, {})
    env.set_txns([])
    envelope = envelopes_service.create_envelope("Emergency fund")
    assert envelope["category_ids"] == []
    others_id = envelopes_service.groups_store.ensure_others()["id"]
    assert envelope["group_id"] == others_id  # falls back to Others
    assert envelope["archived"] is False


def test_create_envelope_blank_name_refused(env):
    env.w(env.envelopes_path, [])
    with pytest.raises(ValidationError):
        envelopes_service.create_envelope("  ")


def test_create_envelope_rejects_duplicate_active_name(env):
    """Envelope names are unique among NON-ARCHIVED envelopes — a duplicate is refused with a
    ConflictError (409), case- and whitespace-insensitively."""
    env.w(env.envelopes_path, [dict(_ENV_FOOD, name="Vacation")])
    env.w(env.assign_path, {})
    env.set_txns([])
    with pytest.raises(ConflictError):
        envelopes_service.create_envelope("Vacation")
    with pytest.raises(ConflictError):
        envelopes_service.create_envelope("  vacation ")


def test_create_envelope_allows_name_of_archived_envelope(env):
    """An ARCHIVED envelope is a tombstone — its name never blocks a fresh active one (delete
    'Utilities', later make a new 'Utilities' — the exact live-book scenario)."""
    env.w(env.envelopes_path, [dict(_ENV_FOOD, name="Utilities", category_ids=[], archived=True)])
    env.w(env.assign_path, {})
    env.set_txns([])
    envelope = envelopes_service.create_envelope("Utilities")
    assert envelope["name"] == "Utilities" and envelope["archived"] is False


def test_create_envelope_enforce_unique_false_bypasses_guard(env):
    """The bot-managed commitment sync passes enforce_unique=False so the idempotent
    page-load sync can never throw on a name collision (its identity is the envelope_id FK)."""
    env.w(env.envelopes_path, [dict(_ENV_FOOD, name="Rent")])
    env.w(env.assign_path, {})
    env.set_txns([])
    envelope = envelopes_service.create_envelope("Rent", enforce_unique=False)
    assert envelope["name"] == "Rent"


def test_rename_envelope_rejects_duplicate_active_name(env):
    """Renaming an envelope onto a live sibling's name is refused; a self-rename (same name,
    different case) is a no-op, never a self-clash."""
    other = dict(_ENV_FOOD, id="env_other00001", name="Bills", category_ids=[], order=1)
    env.w(env.envelopes_path, [_ENV_FOOD, other])
    with pytest.raises(ConflictError):
        envelopes_service.rename_envelope("env_food0000001", name="Bills")
    with pytest.raises(ConflictError):
        envelopes_service.rename_envelope("env_food0000001", name="  bills ")
    out = envelopes_service.rename_envelope("env_food0000001", name="food")
    assert out["ok"] is True and out["envelope"]["name"] == "food"


def test_create_envelope_placed_last_in_named_group(env):
    grp_a = {"id": "grpA", "name": "Group A", "collapsed": False, "order": 0}
    env.w(env.groups_path, [grp_a])
    env.w(env.envelopes_path, [dict(_ENV_FOOD, group_id="grpA", order=0)])
    envelope = envelopes_service.create_envelope("Vacation fund", "grpA")
    assert envelope["group_id"] == "grpA"
    assert envelope["order"] > 0  # after the pre-existing food envelope


def test_apply_envelope_create_never_mints_a_category(env):
    """The whole point of the full split: creating an envelope creates NO category."""
    env.w(env.envelopes_path, [])
    env.w(env.assign_path, {})
    env.set_txns([])
    result = planning.apply_budget_change({"type": "envelope_create", "name": "Sinking fund"})
    assert result["ok"] is True
    assert result["envelope"]["category_ids"] == []
    assert envelopes_service.env_of() == {}  # no category was ever linked


def test_zero_category_envelope_renders_a_row_keyed_by_its_own_id(env):
    """Row identity = envelope id for a zero-category row (RFC full-split §Backend 3 +
    contract addendum): assigned/target/available all work; activity is always 0."""
    env.w(env.groups_path, [])
    envelope = envelopes_service.create_envelope("Emergency fund")
    env.set_txns([_income(1000.0)])

    planning.apply_budget_change(
        {"type": "assign", "month": "2026-06", "envelope_id": envelope["id"], "amount": 500.0}
    )
    table = planning.budget_month("2026-06")
    row = _row(table, envelope["id"])
    assert row["key"] == envelope["id"]
    assert row["envelope_id"] == envelope["id"]
    assert row["category_ids"] == []
    assert row["label"] == "Emergency fund"
    assert row["assigned"] == 500.0
    assert row["activity"] == 0.0
    assert row["available"] == 500.0

    planning.apply_budget_change(
        {
            "type": "set_target",
            "envelope_id": envelope["id"],
            "target_type": "monthly",
            "amount": 500.0,
        }
    )
    row = _row(planning.budget_month("2026-06"), envelope["id"])
    assert row["target"]["type"] == "monthly"
    assert row["target"]["amount"] == 500.0
    assert row["status"] == "funded"


# ── Full split — envelope_add_category (contract addendum) ────────────────────────
# NOTE: this fixture does NOT isolate CategoryStore (long-standing pattern — see the `env`
# fixture note). Uniqueness across this file's test functions is no longer a concern
# (category-opaque-ids migration): create_category() always mints its OWN fresh opaque id,
# so a shared, un-isolated store within one session can never collide.


def test_add_category_links_an_unlinked_category(env):
    env.w(env.envelopes_path, [])
    env.w(env.assign_path, {})
    env.set_txns([])
    envelope = envelopes_service.create_envelope("Groceries")
    created = categories_service.create_category("Groceries", "expense")
    cat_id = created["id"]
    assert envelopes_service.env_of().get(cat_id) is None

    result = envelopes_service.add_category(envelope["id"], cat_id)
    assert result["ok"] is True
    assert envelopes_service.env_of()[cat_id] == envelope["id"]
    updated = envelopes_service.envelope_store.find_by_id(envelope["id"])
    assert updated["category_ids"] == [cat_id]


def test_add_category_folds_legacy_remnant_no_money_dropped(env):
    env.w(env.envelopes_path, [])
    envelope = envelopes_service.create_envelope("Groceries")
    created = categories_service.create_category("Groceries", "expense")
    cat_id = created["id"]
    # A legacy category-keyed remnant (e.g. a bot-managed write) sitting under the bare key.
    env.w(env.assign_path, {"2026-06": {cat_id: 75.0}})
    env.set_txns([])

    envelopes_service.add_category(envelope["id"], cat_id)

    raw = env.r(env.assign_path)["2026-06"]
    assert raw.get(envelope["id"]) == 75.0
    assert cat_id not in raw


def test_add_category_refuses_unknown_envelope(env):
    env.w(env.envelopes_path, [])
    created = categories_service.create_category("Groceries", "expense")
    with pytest.raises(NotFoundError):
        envelopes_service.add_category("env_nope", created["id"])


def test_add_category_refuses_unknown_category(env):
    env.w(env.envelopes_path, [])
    envelope = envelopes_service.create_envelope("Groceries")
    with pytest.raises(NotFoundError):
        envelopes_service.add_category(envelope["id"], "not_a_real_category")


def test_add_category_refuses_already_linked_category(env):
    """The category already belongs to SOME envelope — use merge/split instead."""
    created = categories_service.create_category("Groceries", "expense")
    cat_id = created["id"]
    linked = dict(_ENV_FOOD, category_ids=[cat_id])
    env.w(env.envelopes_path, [linked])
    env.w(env.assign_path, {})
    target = envelopes_service.create_envelope("Groceries 2")
    with pytest.raises(ConflictError):
        envelopes_service.add_category(target["id"], cat_id)


def test_apply_envelope_add_category_choke_point(env):
    env.w(env.envelopes_path, [])
    env.w(env.assign_path, {})
    env.set_txns([])
    envelope = envelopes_service.create_envelope("Groceries")
    created = categories_service.create_category("Groceries", "expense")
    cat_id = created["id"]
    result = planning.apply_budget_change(
        {
            "type": "envelope_add_category",
            "envelope_id": envelope["id"],
            "category_id": cat_id,
        }
    )
    assert result["ok"] is True
    assert envelopes_service.env_of()[cat_id] == envelope["id"]


# ── Full split — "Unbudgeted" synthetic group (RFC §Backend 6) ────────────────────


def test_unbudgeted_group_surfaces_unlinked_category_activity(env):
    env.w(env.envelopes_path, [])
    env.w(env.groups_path, [])
    created = categories_service.create_category("Dining", "expense")
    dining_id = created["id"]
    assert envelopes_service.env_of().get(dining_id) is None
    env.w(env.assign_path, {})
    env.set_txns([_income(1000.0), _expense(dining_id, 45.0)])

    table = planning.budget_month("2026-06")
    unbudgeted = next(g for g in table["groups"] if g.get("unbudgeted"))
    assert unbudgeted["id"] == "__unbudgeted__"
    line = unbudgeted["categories"][0]
    assert line["key"] == dining_id
    assert line["activity"] == -45.0
    assert line["assigned"] == 0.0
    assert line["status"] == "unlinked"
    # The 45 left cash, and the unlinked category HOLDS its own hole until the roll (ADR
    # 2026-09-10) — so `available` is a real figure, RTA does not move this month, and the
    # charge lands in July when the hold resets. The blank-category "Uncategorized" bucket behaves
    # identically: one rule, two buckets.
    assert line["available"] == -45.0
    assert table["held_outside_envelopes"] == -45.0
    assert table["ready_to_assign"] == 1000.0
    assert planning.budget_month("2026-07")["ready_to_assign"] == 1000.0 - 45.0


def test_totals_are_the_envelope_rows_only(env):
    """ADR 2026-09-08 replaced the BE-TOTALS-UNBUDGETED-CONTRACT fold (2026-07-04) and ADR
    2026-09-09 keeps it: totals.activity/available are a PLAIN Σ over envelope ROWS. Uncategorized
    and Unbudgeted are display rows — neither holds anything, so folding them into "what the
    envelopes hold" made the header disagree with the `held_in_envelopes` sitting next to it,
    which is exactly this same Σ. Both groups still RENDER."""
    env.w(env.envelopes_path, [_ENV_FOOD])
    env.w(env.groups_path, [])
    created = categories_service.create_category("Dining", "expense")
    dining_id = created["id"]
    env.w(env.assign_path, {"2026-06": {"env_food0000001": 50.0}})
    env.set_txns(
        [
            _income(1000.0),
            _expense("cat_13095ba70000", 30.0),  # a normal budgeted row
            _expense(dining_id, 45.0),  # unbudgeted (unlinked) — a display row
            _expense("", 10.0),  # unsorted — a display row
        ],
    )
    table = planning.budget_month("2026-06")
    rows = [
        c
        for g in table["groups"]
        for c in g["categories"]
        if not g.get("unsorted") and not g.get("unbudgeted")
    ]
    assert round(sum(c["activity"] for c in rows), 2) == table["totals"]["activity"]
    assert round(sum(c["available"] for c in rows), 2) == table["totals"]["available"]
    # food alone: −30 activity, 50 − 30 = 20 available.
    assert table["totals"]["activity"] == pytest.approx(-30.0, abs=0.01)
    assert table["totals"]["available"] == pytest.approx(20.0, abs=0.01)
    # Both display groups are still on the wire.
    assert any(g.get("unsorted") for g in table["groups"])
    assert any(g.get("unbudgeted") for g in table["groups"])
    # The identity: cash (1000 − 85 = 915) − held_in_envelopes (food's 50 − 30 = 20)
    # − held_outside_envelopes (−10 unsorted + −45 unbudgeted = −55) = 950. Neither display row
    # is an ENVELOPE row, so neither is in `totals` or `held_in_envelopes` — but each HOLDS its
    # own hole until the roll (ADR 2026-09-10), in the identity's own separate term.
    assert table["held_in_envelopes"] == pytest.approx(20.0, abs=0.01)
    assert table["held_outside_envelopes"] == pytest.approx(-55.0, abs=0.01)
    assert table["ready_to_assign"] == pytest.approx(950.0, abs=0.01)


def test_unbudgeted_group_omitted_when_empty(env):
    env.w(env.envelopes_path, [])
    env.w(env.groups_path, [])
    env.w(env.assign_path, {})
    env.set_txns([_income(1000.0)])
    table = planning.budget_month("2026-06")
    assert not any(g.get("unbudgeted") for g in table["groups"])


def test_unbudgeted_excludes_linked_and_blank_categories(env):
    """A LINKED category's activity never leaks into Unbudgeted (it has its own row); blank
    stays in "Uncategorized", a separate bucket."""
    env.w(env.envelopes_path, [_ENV_FOOD])
    env.w(env.groups_path, [])
    env.w(env.assign_path, {})
    env.set_txns([_income(1000.0), _expense("cat_13095ba70000", 20.0), _expense("", 10.0)])
    table = planning.budget_month("2026-06")
    assert not any(g.get("unbudgeted") for g in table["groups"])
    unsorted = next(g for g in table["groups"] if g.get("unsorted"))
    assert unsorted["categories"][0]["activity"] == -10.0


# ── Budget-envelopes handoff port (Lane BE §2-3) — delete / archive / restore ──────


def test_delete_envelope_detaches_zeroes_and_hard_deletes(env):
    """ADR 2026-09-26-deleting-an-envelope-deletes-it: delete is for real — the row, its
    target/note/cap and every month's assignment are gone, never archived. The one thing that
    survives is a server-held Undo snapshot named by ``undo_token`` (see the
    ``test_undo_delete_envelope_*`` tests below for the replay half)."""
    env.w(env.envelopes_path, [_ENV_FOOD])
    env.w(
        env.assign_path,
        {"2026-05": {"env_food0000001": 100.0}, "2026-06": {"env_food0000001": 300.0}},
    )
    env.set_txns([_income(1000.0)])
    planning.apply_budget_change(
        {
            "type": "set_target",
            "envelope_id": "env_food0000001",
            "target_type": "monthly",
            "amount": 300.0,
        }
    )
    planning.write_category_note("env_food0000001", "grocery money")

    result = envelopes_service.delete_envelope("env_food0000001")
    assert result["ok"] is True
    assert result["undo_token"]
    assert result["former_category_ids"] == ["cat_13095ba70000"]
    assert result["zeroed_assignments"] == {"2026-05": 100.0, "2026-06": 300.0}

    # Hard-deleted for real — the row is simply gone, not left behind with archive metadata.
    assert envelopes_service.envelope_store.find_by_id("env_food0000001") is None

    raw = env.r(env.assign_path)
    assert not raw.get("2026-05", {}).get("env_food0000001")
    assert not raw.get("2026-06", {}).get("env_food0000001")
    assert planning.read_category_targets().get("env_food0000001") is None
    assert planning.read_category_notes().get("env_food0000001") is None
    assert envelopes_service.env_of().get("cat_13095ba70000") is None


def test_undo_delete_envelope_replays_the_whole_snapshot(env):
    """The server-held Undo (ADR 2026-09-26-deleting-an-envelope-deletes-it) puts back
    EXACTLY what the delete erased: same id, target, note, 50/30/20 cap, every month's
    assignment, and re-links its former category (still unlinked)."""
    grp = {"id": "grpA", "name": "Group A", "collapsed": False, "order": 0}
    env.w(env.groups_path, [grp])
    env.w(env.envelopes_path, [dict(_ENV_FOOD, group_id="grpA")])
    env.w(
        env.assign_path,
        {"2026-05": {"env_food0000001": 100.0}, "2026-06": {"env_food0000001": 300.0}},
    )
    env.set_txns([_income(1000.0)])
    planning.apply_budget_change(
        {
            "type": "set_target",
            "envelope_id": "env_food0000001",
            "target_type": "monthly",
            "amount": 300.0,
        }
    )
    planning.write_category_note("env_food0000001", "grocery money")
    envelopes_service.engine_state_store.set(
        "proportional", {"caps": {"env_food0000001": [["2026-05", 250.0]]}}
    )

    result = envelopes_service.delete_envelope("env_food0000001")
    undo = envelopes_service.undo_delete_envelope(result["undo_token"])

    assert undo["ok"] is True
    assert undo["relinked"] == ["cat_13095ba70000"]
    envelope = envelopes_service.envelope_store.find_by_id("env_food0000001")
    assert envelope is not None
    assert envelope["name"] == "Food"
    assert envelope["group_id"] == "grpA"  # its stored group, still alive
    assert envelope["category_ids"] == ["cat_13095ba70000"]
    assert envelope["archived"] is False
    assert envelopes_service.env_of()["cat_13095ba70000"] == "env_food0000001"

    raw = env.r(env.assign_path)
    assert raw["2026-05"]["env_food0000001"] == 100.0
    assert raw["2026-06"]["env_food0000001"] == 300.0
    assert planning.read_category_targets()["env_food0000001"] == {
        "type": "monthly",
        "amount": 300.0,
    }
    assert planning.read_category_notes()["env_food0000001"] == "grocery money"
    assert envelopes_service.engine_state_store.get("proportional")["caps"] == {
        "env_food0000001": [["2026-05", 250.0]]
    }


def test_undo_delete_envelope_is_one_shot(env):
    """A second undo — or a stale token from another tab — raises ConflictError and writes
    nothing, mirroring undo_fresh_start's exact one-shot contract."""
    env.w(env.envelopes_path, [_ENV_FOOD])
    env.w(env.assign_path, {})
    env.set_txns([])
    result = envelopes_service.delete_envelope("env_food0000001")
    envelopes_service.undo_delete_envelope(result["undo_token"])

    with pytest.raises(ConflictError):
        envelopes_service.undo_delete_envelope(result["undo_token"])


def test_undo_delete_envelope_unknown_token_raises_conflict(env):
    env.w(env.envelopes_path, [])
    with pytest.raises(ConflictError):
        envelopes_service.undo_delete_envelope("edu_nope")


def test_undo_delete_envelope_refuses_a_name_taken_since(env):
    """A same-named envelope made after the delete would leave two live 'Food's, which every
    other create/rename path refuses — so undo refuses too, writes nothing, and keeps its token
    for a retry once the newcomer is renamed (review, 2026-09-26)."""
    env.w(env.envelopes_path, [_ENV_FOOD])
    env.w(env.assign_path, {})
    env.set_txns([])
    result = envelopes_service.delete_envelope("env_food0000001")
    fresh = envelopes_service.create_envelope("food")

    with pytest.raises(ConflictError):
        envelopes_service.undo_delete_envelope(result["undo_token"])
    live = [e["name"] for e in envelopes_service.envelope_store.all() if not e.get("archived")]
    assert live == ["food"]

    envelopes_service.rename_envelope(fresh["id"], "Groceries")
    undo = envelopes_service.undo_delete_envelope(result["undo_token"])
    assert undo["envelope"]["name"] == "Food"


def test_undo_delete_envelope_skips_a_category_already_linked_elsewhere(env):
    """A former member manually relinked to a DIFFERENT envelope during the undo window must
    not be stolen back — undo only relinks members that are CURRENTLY unlinked (mirrors the
    retired restore_envelope's own rule)."""
    env.w(env.envelopes_path, [_ENV_FOOD])
    env.w(env.assign_path, {})
    env.set_txns([])
    result = envelopes_service.delete_envelope("env_food0000001")
    other = envelopes_service.create_envelope("Other pot")
    envelopes_service.add_category(other["id"], "cat_13095ba70000")

    undo = envelopes_service.undo_delete_envelope(result["undo_token"])
    assert undo["relinked"] == []
    assert envelopes_service.env_of()["cat_13095ba70000"] == other["id"]
    restored = envelopes_service.envelope_store.find_by_id("env_food0000001")
    assert restored["category_ids"] == []


def test_undo_delete_envelope_group_kept_if_alive_else_others(env):
    grp = {"id": "grpA", "name": "Group A", "collapsed": False, "order": 0}
    env.w(env.groups_path, [grp])
    env.w(env.envelopes_path, [dict(_ENV_FOOD, group_id="grpA")])
    env.w(env.assign_path, {})
    env.set_txns([])
    result = envelopes_service.delete_envelope("env_food0000001")
    undo = envelopes_service.undo_delete_envelope(result["undo_token"])
    assert undo["envelope"]["group_id"] == "grpA"  # group still exists — kept

    env.w(env.envelopes_path, envelopes_service.envelope_store.all() + [dict(_ENV_TRANSPORT)])
    result2 = envelopes_service.delete_envelope("env_trans000001")
    env.w(env.groups_path, [])  # the group vanished
    undo2 = envelopes_service.undo_delete_envelope(result2["undo_token"])
    others = envelopes_service.groups_store.ensure_others()
    assert undo2["envelope"]["group_id"] == others["id"]


def test_delete_envelope_releases_reserve_to_rta(env):
    env.w(env.envelopes_path, [_ENV_FOOD])
    env.w(env.assign_path, {"2026-06": {"env_food0000001": 300.0}})
    env.set_txns([_income(1000.0)])
    before = planning.budget_month("2026-06")["ready_to_assign"]
    envelopes_service.delete_envelope("env_food0000001")
    after = planning.budget_month("2026-06")["ready_to_assign"]
    assert after == round(before + 300.0, 2)


def test_delete_envelope_refuses_already_archived(env):
    archived = dict(_ENV_FOOD, archived=True)
    env.w(env.envelopes_path, [archived])
    env.w(env.assign_path, {})
    env.set_txns([])
    with pytest.raises(ValidationError):
        envelopes_service.delete_envelope("env_food0000001")


def test_delete_envelope_refuses_commitments_synced(env, monkeypatch, tmp_path):
    """category-opaque-ids migration (decision #4): see test_apply_envelope_merge_refuses_
    commitments_synced above for the FK rationale."""
    from backend.db.commitments_store import CommitmentsStore

    # CommitmentsStore is DB-backed now (plan 4a task 3) — db_isolated (via env) covers it.
    commit_env = dict(_ENV_FOOD, category_ids=[])
    env.w(env.envelopes_path, [commit_env])
    env.w(env.assign_path, {})
    env.set_txns([])
    CommitmentsStore().create({"id": "abc123", "name": "Rent"})
    CommitmentsStore().update_commitment("abc123", {"envelope_id": "env_food0000001"})
    with pytest.raises(ValidationError):
        envelopes_service.delete_envelope("env_food0000001")


def test_delete_envelope_unknown_raises_not_found(env):
    env.w(env.envelopes_path, [])
    with pytest.raises(NotFoundError):
        envelopes_service.delete_envelope("env_nope")


def test_deleted_envelope_category_surfaces_in_unbudgeted(env):
    env.w(env.envelopes_path, [_ENV_FOOD])
    env.w(env.assign_path, {"2026-06": {"env_food0000001": 300.0}})
    env.set_txns([_income(1000.0), _expense("cat_13095ba70000", 45.0)])
    envelopes_service.delete_envelope("env_food0000001")
    table = planning.budget_month("2026-06")
    unbudgeted = next(g for g in table["groups"] if g.get("unbudgeted"))
    assert unbudgeted["categories"][0]["key"] == "cat_13095ba70000"
    assert unbudgeted["categories"][0]["activity"] == -45.0


def test_apply_envelope_delete_choke_point(env):
    env.w(env.envelopes_path, [_ENV_FOOD])
    env.w(env.assign_path, {"2026-06": {"env_food0000001": 200.0}})
    env.set_txns([])
    result = planning.apply_budget_change(
        {"type": "envelope_delete", "envelope_id": "env_food0000001"}
    )
    assert result["ok"] is True
    assert result["undo_token"]
    assert result["zeroed_assignments"] == {"2026-06": 200.0}


def test_future_assigned_does_not_leak_after_delete(env):
    """Code-map callout: _future_assigned sums raw assignment values across all future
    months — it must not leak a deleted envelope's future-month assignment once delete_envelope
    zeroes it (it reads through the SAME raw assignments_store, no envelope filtering)."""
    env.w(env.envelopes_path, [_ENV_FOOD])
    env.w(env.assign_path, {"2026-07": {"env_food0000001": 300.0}})
    env.set_txns([_income(1000.0)])
    assert planning.budget_month("2026-06")["assigned_in_future_months"] == 300.0
    envelopes_service.delete_envelope("env_food0000001")
    assert planning.budget_month("2026-06")["assigned_in_future_months"] == 0.0


# ── BE-PHANTOM-TARGET-ON-RESTORE fix (2026-07-04) — the OTHER two archive paths ────
# delete_envelope/merge_envelope already clear the env-keyed target/note on archive;
# on_category_deleted's last-member archive and on_category_renamed's sole-member
# fold-archive must do the same, or restoring resurrects a phantom target/note on an
# empty pot and auto-assign 'underfunded' funds it with real money.


def test_on_category_deleted_last_member_clears_target_and_note(env):
    env.w(env.envelopes_path, [_ENV_FOOD])
    env.w(env.assign_path, {"2026-06": {"env_food0000001": 300.0}})
    env.set_txns([])
    planning.apply_budget_change(
        {
            "type": "set_target",
            "envelope_id": "env_food0000001",
            "target_type": "monthly",
            "amount": 80.0,
        }
    )
    planning.write_category_note("env_food0000001", "gym money")

    result = envelopes_service.on_category_deleted("cat_13095ba70000")
    assert result["envelopes"] == 1

    # Hard-deleted for real (ADR 2026-09-26-deleting-an-envelope-deletes-it) — no more archive,
    # so there is no row left to resurrect a phantom target onto; its target/note must simply
    # be gone from the profile store, never left orphaned under a dead id.
    assert envelopes_service.envelope_store.find_by_id("env_food0000001") is None
    assert planning.read_category_targets().get("env_food0000001") is None
    assert planning.read_category_notes().get("env_food0000001") is None


def test_on_category_renamed_sole_member_merge_clears_target_and_note(env):
    """The 'true merge of two mapped categories, old_key is env_old's sole member' branch
    (a fold-into-target-and-archive, functionally a merge) must clear env_old's OWN
    target/note exactly like merge_envelope — its money already moved to the target."""
    gym_env = dict(_ENV_FOOD, id="env_gym0000001", name="Gym", category_ids=["gym"])
    fitness_env = dict(
        _ENV_TRANSPORT, id="env_fit00000001", name="Fitness", category_ids=["fitness"]
    )
    env.w(env.envelopes_path, [gym_env, fitness_env])
    env.w(env.assign_path, {"2026-06": {"env_gym0000001": 300.0, "env_fit00000001": 100.0}})
    env.set_txns([])
    planning.apply_budget_change(
        {
            "type": "set_target",
            "envelope_id": "env_gym0000001",
            "target_type": "monthly",
            "amount": 50.0,
        }
    )
    planning.write_category_note("env_gym0000001", "yoga membership")

    result = envelopes_service.on_category_renamed("gym", "fitness", merged=True)
    assert result["envelopes"] == 2

    # Hard-deleted for real (ADR 2026-09-26-deleting-an-envelope-deletes-it) — its target/note
    # are gone, never left orphaned under a dead id for a since-retired restore to resurrect.
    assert envelopes_service.envelope_store.find_by_id("env_gym0000001") is None
    assert planning.read_category_targets().get("env_gym0000001") is None
    assert planning.read_category_notes().get("env_gym0000001") is None
    raw = env.r(env.assign_path)["2026-06"]
    assert raw["env_fit00000001"] == 400.0  # money conserved: 300 + 100


def test_envelope_restore_op_is_retired_from_the_choke(env):
    """ADR 2026-09-26-deleting-an-envelope-deletes-it retires the envelope_restore
    apply_budget_change type outright — there is nothing left to un-archive. The choke falls
    through to its generic unknown-type refusal, exactly as for any other dead op name."""
    env.w(env.envelopes_path, [_ENV_FOOD])
    env.w(env.assign_path, {})
    env.set_txns([])
    envelopes_service.delete_envelope("env_food0000001")
    with pytest.raises(ValidationError, match="unknown budget change type"):
        planning.apply_budget_change({"type": "envelope_restore", "envelope_id": "env_food0000001"})


def test_merge_envelope_hard_deletes_the_source(env):
    """ADR 2026-09-26-deleting-an-envelope-deletes-it: merge's source is deleted for real —
    there is no archive metadata left to stamp, because there is no row left to stamp it on."""
    env.w(env.envelopes_path, [_ENV_FOOD, _ENV_TRANSPORT])
    env.w(env.assign_path, {})
    env.set_txns([])
    envelopes_service.merge_envelope("env_food0000001", "env_trans000001")
    assert envelopes_service.envelope_store.find_by_id("env_food0000001") is None


def test_list_archived_envelopes_shape_and_resolves_labels(env):
    """list_archived_envelopes is now a permanently-historical read (ADR
    2026-09-26-deleting-an-envelope-deletes-it) — delete/merge hard-delete for real, so nothing
    NEW ever lands here. Seeded directly, exactly as a live book's leftover pre-hard-delete
    archived rows are shaped."""
    env.w(env.envelopes_path, [_ENV_FOOD, _ENV_TRANSPORT])
    env.w(env.assign_path, {})
    env.set_txns([])
    target = envelopes_service.create_envelope("Groceries 2")
    envelopes_service.envelope_store.update_envelope(
        "env_food0000001",
        {
            "archived": True,
            "archive_reason": "deleted",
            "former_category_ids": ["cat_13095ba70000"],
            "category_ids": [],
            "archived_at": "2026-01-01T00:00:00",
        },
    )
    envelopes_service.envelope_store.update_envelope(
        "env_trans000001",
        {
            "archived": True,
            "archive_reason": "merged",
            "merged_into": target["id"],
            "former_category_ids": ["cat_db5642ab0000"],
            "category_ids": [],
            "archived_at": "2026-01-02T00:00:00",
        },
    )

    rows = envelopes_service.list_archived_envelopes()
    by_id = {r["id"]: r for r in rows}
    assert set(by_id) == {"env_food0000001", "env_trans000001"}

    deleted_row = by_id["env_food0000001"]
    assert deleted_row["archive_reason"] == "deleted"
    assert deleted_row["merged_into"] == ""
    assert deleted_row["merged_into_name"] == ""
    assert deleted_row["former_category_ids"] == ["cat_13095ba70000"]
    assert deleted_row["former_members"] == [{"key": "cat_13095ba70000", "label": "Food & Dining"}]

    merged_row = by_id["env_trans000001"]
    assert merged_row["archive_reason"] == "merged"
    assert merged_row["merged_into"] == target["id"]
    assert merged_row["merged_into_name"] == "Groceries 2"
    assert merged_row["former_category_ids"] == ["cat_db5642ab0000"]


def test_list_archived_envelopes_newest_first(env):
    env.w(env.envelopes_path, [_ENV_FOOD, _ENV_TRANSPORT])
    env.w(env.assign_path, {})
    env.set_txns([])
    envelopes_service.envelope_store.update_envelope(
        "env_food0000001",
        {"archived": True, "archive_reason": "deleted", "archived_at": "2026-01-01T00:00:00"},
    )
    envelopes_service.envelope_store.update_envelope(
        "env_trans000001",
        {"archived": True, "archive_reason": "deleted", "archived_at": "2026-06-01T00:00:00"},
    )
    rows = envelopes_service.list_archived_envelopes()
    assert [r["id"] for r in rows] == ["env_trans000001", "env_food0000001"]


def test_list_archived_envelopes_no_metadata_sorts_last(env):
    """Pre-existing archives (archived before this metadata shipped) have no archived_at —
    they still render (reason "", restore-empty only) but sort AFTER any dated archive."""
    env.w(env.envelopes_path, [_ENV_FOOD, _ENV_TRANSPORT])
    env.w(env.assign_path, {})
    env.set_txns([])
    envelopes_service.envelope_store.update_envelope("env_food0000001", {"archived": True})
    envelopes_service.envelope_store.update_envelope(
        "env_trans000001",
        {"archived": True, "archive_reason": "deleted", "archived_at": "2026-06-01T00:00:00"},
    )
    rows = envelopes_service.list_archived_envelopes()
    assert [r["id"] for r in rows] == ["env_trans000001", "env_food0000001"]
    assert rows[1]["archive_reason"] == ""


# ── Budget-envelopes handoff port (Lane BE §7) — envelope color ────────────────────


def test_create_envelope_with_color(env):
    env.w(env.envelopes_path, [])
    envelope = envelopes_service.create_envelope("Emergency fund", color="#3B86DD")
    assert envelope["color"] == "#3b86dd"  # lowercased


def test_create_envelope_default_color_blank(env):
    env.w(env.envelopes_path, [])
    envelope = envelopes_service.create_envelope("Emergency fund")
    assert envelope["color"] == ""


def test_create_envelope_invalid_color_refused(env):
    env.w(env.envelopes_path, [])
    with pytest.raises(ValidationError):
        envelopes_service.create_envelope("Emergency fund", color="not-a-color")


def test_rename_envelope_sets_color_only(env):
    env.w(env.envelopes_path, [_ENV_FOOD])
    result = envelopes_service.rename_envelope("env_food0000001", color="#00ff00")
    assert result["envelope"]["color"] == "#00ff00"
    assert result["envelope"]["name"] == "Food"  # name unchanged


def test_rename_envelope_name_and_color_together(env):
    env.w(env.envelopes_path, [_ENV_FOOD])
    result = envelopes_service.rename_envelope("env_food0000001", "Groceries", "#00ff00")
    assert result["envelope"]["name"] == "Groceries"
    assert result["envelope"]["color"] == "#00ff00"


def test_rename_envelope_neither_field_refused(env):
    env.w(env.envelopes_path, [_ENV_FOOD])
    with pytest.raises(ValidationError):
        envelopes_service.rename_envelope("env_food0000001")


def test_rename_envelope_invalid_color_refused(env):
    env.w(env.envelopes_path, [_ENV_FOOD])
    with pytest.raises(ValidationError):
        envelopes_service.rename_envelope("env_food0000001", color="red")


def test_budget_month_row_emits_envelope_color(env):
    colored = dict(_ENV_FOOD, color="#e0a032")
    env.w(env.envelopes_path, [colored])
    env.w(env.assign_path, {})
    env.set_txns([])
    row = _row(planning.budget_month("2026-06"), "cat_13095ba70000")
    assert row["color"] == "#e0a032"


def _colorless_envelope_over(env, category_id: str) -> None:
    env.w(
        env.envelopes_path,
        [
            {
                "id": "env_colorless01",
                "name": "Colorless",
                "category_ids": [category_id],
                "group_id": "others",
                "order": 0,
                "archived": False,
            }
        ],
    )
    env.w(env.assign_path, {})
    env.set_txns([])


def test_budget_month_row_color_inherits_a_new_categorys_auto_color(env):
    """A category created without a colour gets one from the palette (owner, 2026-09-25), so an
    envelope over it with no colour of its own shows that colour — just like an envelope over a
    BUILTIN_CATEGORIES member with its default colour (above)."""
    created = categories_service.create_category("Uncolored", "expense")
    assert created["color"] in categories_service.CATEGORY_PALETTE
    _colorless_envelope_over(env, created["id"])
    row = _row(planning.budget_month("2026-06"), created["id"])
    assert row["color"] == created["color"]


def test_budget_month_row_color_blank_over_a_legacy_colorless_category(env):
    """A row written before auto-colour can still carry no colour — an envelope over it with no
    colour of its own must render blank, not inherit anything."""
    CategoryStore().insert(
        {"id": "cat_legacy000001", "label": "Legacy", "kind": "expense", "color": ""}
    )
    _colorless_envelope_over(env, "cat_legacy000001")
    row = _row(planning.budget_month("2026-06"), "cat_legacy000001")
    assert row["color"] == ""


# ── Budget-envelopes handoff port (Lane BE §8) — RTA as a move endpoint ────────────


def test_move_money_from_rta_funds_target_no_cap(env):
    env.w(env.envelopes_path, [_ENV_FOOD])
    env.w(env.assign_path, {})
    env.set_txns([])
    result = planning.apply_budget_change(
        {
            "type": "move_money",
            "month": "2026-06",
            "from_envelope_id": "__rta",
            "to_envelope_id": "env_food0000001",
            "amount": 500.0,
        }
    )
    assert result == {
        "ok": True,
        "month": "2026-06",
        "from_envelope_id": "__rta",
        "to_envelope_id": "env_food0000001",
        "to_assigned": 500.0,
    }
    assert planning._assigned_amt("2026-06", "env_food0000001") == 500.0
    # RTA may go negative — no cap, exactly like assign_budget.
    assert planning.budget_month("2026-06")["ready_to_assign"] == -500.0


def test_move_money_to_rta_releases_source_capped(env):
    env.w(env.envelopes_path, [_ENV_FOOD])
    env.w(env.assign_path, {"2026-06": {"env_food0000001": 300.0}})
    env.set_txns([])
    result = planning.apply_budget_change(
        {
            "type": "move_money",
            "month": "2026-06",
            "from_envelope_id": "env_food0000001",
            "to_envelope_id": "__rta",
            "amount": 100.0,
        }
    )
    assert result == {
        "ok": True,
        "month": "2026-06",
        "from_envelope_id": "env_food0000001",
        "to_envelope_id": "__rta",
        "from_assigned": 200.0,
    }
    assert planning._assigned_amt("2026-06", "env_food0000001") == 200.0


def test_move_money_to_rta_capped_at_available(env):
    """FEB-02 fix (2026-07-04): the release-to-RTA cap is the row's AVAILABLE, not this-month
    assigned — here they coincide (no carry, no activity), so 100 > available(30) is still
    refused."""
    env.w(env.envelopes_path, [_ENV_FOOD])
    env.w(env.assign_path, {"2026-06": {"env_food0000001": 30.0}})
    env.set_txns([])
    with pytest.raises(ValidationError, match="cannot move more than .* has available"):
        planning.apply_budget_change(
            {
                "type": "move_money",
                "month": "2026-06",
                "from_envelope_id": "env_food0000001",
                "to_envelope_id": "__rta",
                "amount": 100.0,
            }
        )
    assert planning._assigned_amt("2026-06", "env_food0000001") == 30.0  # untouched


def test_move_money_both_rta_refused(env):
    env.w(env.envelopes_path, [])
    env.w(env.assign_path, {})
    env.set_txns([])
    with pytest.raises(ValidationError):
        planning.apply_budget_change(
            {
                "type": "move_money",
                "month": "2026-06",
                "from_envelope_id": "__rta",
                "to_envelope_id": "__rta",
                "amount": 10.0,
            }
        )


# ── Budget-envelopes handoff port (Lane BE §9) — target deadline fields ────────────


def test_weekly_target_due_weekday_changes_multiplier(env):
    """2026-06 has 5 Mondays but only 4 Saturdays — due_weekday=5 (Saturday) must use the
    Saturday count, not the default Monday count."""
    env.w(env.envelopes_path, [_ENV_FOOD])
    env.w(env.assign_path, {})
    env.set_txns([])
    planning.apply_budget_change(
        {
            "type": "set_target",
            "envelope_id": "env_food0000001",
            "target_type": "weekly",
            "amount": 100.0,
            "due_weekday": 5,
        }
    )
    row = _row(planning.budget_month("2026-06"), "cat_13095ba70000")
    assert row["target"]["due_weekday"] == 5
    assert row["needed_this_month"] == 100.0 * planning._weeks_in_month("2026-06", 5)
    assert row["needed_this_month"] != 500.0  # NOT the Monday count


def test_weekly_target_unset_due_weekday_is_byte_identical_to_before(env):
    """Regression pin: an unset due_weekday must reproduce the original Monday-only count
    exactly (2026-06 has 5 Mondays -> $500 needed on a $100/week target)."""
    env.w(env.envelopes_path, [_ENV_FOOD])
    env.w(env.assign_path, {})
    env.set_txns([])
    planning.apply_budget_change(
        {
            "type": "set_target",
            "envelope_id": "env_food0000001",
            "target_type": "weekly",
            "amount": 100.0,
        }
    )
    row = _row(planning.budget_month("2026-06"), "cat_13095ba70000")
    assert row["target"].get("due_weekday") is None
    assert row["needed_this_month"] == 500.0


def test_monthly_target_due_day_stored_and_echoed_no_math_change(env):
    env.w(env.envelopes_path, [_ENV_FOOD])
    env.w(env.assign_path, {})
    env.set_txns([])
    planning.apply_budget_change(
        {
            "type": "set_target",
            "envelope_id": "env_food0000001",
            "target_type": "monthly",
            "amount": 800.0,
            "due_day": 15,
        }
    )
    row = _row(planning.budget_month("2026-06"), "cat_13095ba70000")
    assert row["target"]["due_day"] == 15
    assert row["needed_this_month"] == 800.0  # unaffected by due_day


def test_monthly_target_due_day_last_day_sentinel(env):
    env.w(env.envelopes_path, [_ENV_FOOD])
    env.w(env.assign_path, {})
    env.set_txns([])
    planning.apply_budget_change(
        {
            "type": "set_target",
            "envelope_id": "env_food0000001",
            "target_type": "monthly",
            "amount": 800.0,
            "due_day": -1,
        }
    )
    row = _row(planning.budget_month("2026-06"), "cat_13095ba70000")
    assert row["target"]["due_day"] == -1


def test_yearly_target_date_stored_and_echoed_no_math_change(env):
    env.w(env.envelopes_path, [_ENV_FOOD])
    env.w(env.assign_path, {})
    env.set_txns([])
    planning.apply_budget_change(
        {
            "type": "set_target",
            "envelope_id": "env_food0000001",
            "target_type": "yearly",
            "amount": 1200.0,
            "date": "2026-03-15",
        }
    )
    row = _row(planning.budget_month("2026-06"), "cat_13095ba70000")
    assert row["target"]["date"] == "2026-03-15"
    assert row["needed_this_month"] == 100.0  # unaffected by date


def test_target_due_weekday_refused_on_non_weekly(env):
    env.w(env.envelopes_path, [_ENV_FOOD])
    env.w(env.assign_path, {})
    env.set_txns([])
    with pytest.raises(ValidationError):
        planning.apply_budget_change(
            {
                "type": "set_target",
                "envelope_id": "env_food0000001",
                "target_type": "monthly",
                "amount": 800.0,
                "due_weekday": 2,
            }
        )


def test_target_due_day_refused_on_non_monthly(env):
    env.w(env.envelopes_path, [_ENV_FOOD])
    env.w(env.assign_path, {})
    env.set_txns([])
    with pytest.raises(ValidationError):
        planning.apply_budget_change(
            {
                "type": "set_target",
                "envelope_id": "env_food0000001",
                "target_type": "weekly",
                "amount": 100.0,
                "due_day": 10,
            }
        )


def test_target_date_refused_on_monthly(env):
    env.w(env.envelopes_path, [_ENV_FOOD])
    env.w(env.assign_path, {})
    env.set_txns([])
    with pytest.raises(ValidationError):
        planning.apply_budget_change(
            {
                "type": "set_target",
                "envelope_id": "env_food0000001",
                "target_type": "monthly",
                "amount": 800.0,
                "date": "2026-03-15",
            }
        )


def test_target_due_weekday_out_of_range_refused(env):
    env.w(env.envelopes_path, [_ENV_FOOD])
    env.w(env.assign_path, {})
    env.set_txns([])
    with pytest.raises(ValidationError):
        planning.apply_budget_change(
            {
                "type": "set_target",
                "envelope_id": "env_food0000001",
                "target_type": "weekly",
                "amount": 100.0,
                "due_weekday": 9,
            }
        )


def test_target_due_day_out_of_range_refused(env):
    env.w(env.envelopes_path, [_ENV_FOOD])
    env.w(env.assign_path, {})
    env.set_txns([])
    with pytest.raises(ValidationError):
        planning.apply_budget_change(
            {
                "type": "set_target",
                "envelope_id": "env_food0000001",
                "target_type": "monthly",
                "amount": 800.0,
                "due_day": 29,
            }
        )


# ── target/note remnant hygiene on unlink/merge/delete (code-review catch, 2026-07-04) ────


def test_merge_clears_the_source_envelopes_own_target(env):
    """HIGH repro: merge never cleared the SOURCE envelope's own env-keyed target, so a later
    restore resurrected a stale target on an empty envelope — a phantom "underfunded" row that
    auto-assign would fund with real money. Now that merge hard-deletes the source for real
    (ADR 2026-09-26-deleting-an-envelope-deletes-it) there is no row left to resurrect a
    target onto — but the target must still be gone from profile.category_targets, never left
    orphaned under a dead id forever."""
    env.w(env.envelopes_path, [_ENV_FOOD, _ENV_TRANSPORT])
    env.w(env.assign_path, {})
    env.set_txns([_income(1000.0)])
    planning.write_category_targets({"env_food0000001": {"type": "monthly", "amount": 150.0}})
    envelopes_service.merge_envelope("env_food0000001", "env_trans000001")
    assert planning.read_category_targets().get("env_food0000001") is None


def test_merge_keeps_target_envelopes_own_target(env):
    """The merge TARGET's own target must survive the source-slot pop untouched."""
    env.w(env.envelopes_path, [_ENV_FOOD, _ENV_TRANSPORT])
    env.w(env.assign_path, {})
    env.set_txns([_income(1000.0)])
    planning.write_category_targets(
        {
            "env_food0000001": {"type": "monthly", "amount": 150.0},
            "env_trans000001": {"type": "monthly", "amount": 80.0},
        }
    )
    envelopes_service.merge_envelope("env_food0000001", "env_trans000001")
    targets = planning.read_category_targets()
    assert targets.get("env_trans000001") == {"type": "monthly", "amount": 80.0}
    assert targets.get("env_food0000001") is None


def test_split_leaves_a_stray_bare_key_target_untouched(env):
    """Superseded MED-HIGH repro: targets are envelope-id native end to end (docs/adr/
    2026-07-05-envelope-id-native.md) — split only ever touches category_ids membership, it
    has no bare-key target remnant to fold (that auto-heal machinery, and the boot resyncs it
    protected against, are gone; the one real book's data was migrated directly, once, by a
    since-removed one-shot script). A stray target already sitting under a
    bare category key (never a real envelope id) is simply left exactly where it was."""
    merged = dict(
        _ENV_FOOD, id="env_merged001", category_ids=["cat_13095ba70000", "cat_dfcf9f910000"]
    )
    env.w(env.envelopes_path, [merged])
    env.w(env.assign_path, {"2026-06": {"env_merged001": 400.0}})
    env.set_txns([_income(1000.0)])
    planning.write_category_targets({"cat_dfcf9f910000": {"type": "monthly", "amount": 60.0}})

    envelopes_service.split_category("env_merged001", "cat_dfcf9f910000")

    targets = planning.read_category_targets()
    assert targets.get("cat_dfcf9f910000") == {"type": "monthly", "amount": 60.0}  # left alone
    assert "env_merged001" not in targets  # the row never had one of its own here
    assert envelopes_service.env_of().get("cat_dfcf9f910000") is None  # still genuinely unlinked


def test_delete_envelope_only_clears_its_own_target_slot(env):
    """Same shape, delete flavor: ``delete_envelope`` pops its OWN envelope-id slot only — a
    stray bare-key target is not its business and is left untouched."""
    env.w(env.envelopes_path, [_ENV_FOOD])
    env.w(env.assign_path, {})
    env.set_txns([])
    planning.write_category_targets({"cat_13095ba70000": {"type": "monthly", "amount": 90.0}})

    envelopes_service.delete_envelope("env_food0000001")

    targets = planning.read_category_targets()
    assert targets.get("cat_13095ba70000") == {
        "type": "monthly",
        "amount": 90.0,
    }  # stray, left alone
    assert "env_food0000001" not in targets  # the envelope never had one under its own id


def test_label_only_rename_follows_onto_same_named_1to1_envelope(env):
    """Regression, 2026-07-04: a LABEL-ONLY category rename (key unchanged — the manager's
    rename path) left a same-named 1:1 envelope behind, so the row grew a chip showing the
    new label next to the stale envelope name. The name-follow contract must cover both
    rename paths."""
    env.w(env.envelopes_path, [dict(_ENV_FOOD, name="Food & Dining")])  # matches the seeded label
    env.w(env.assign_path, {})
    env.set_txns([])
    categories_service.rename_category("cat_13095ba70000", new_label="Groceries")
    assert envelopes_service.envelope_store.find_by_id("env_food0000001")["name"] == "Groceries"


def test_label_only_rename_leaves_independently_named_envelope_alone(env):
    """An envelope the user deliberately named ('Fun money' ← Shopping) must NOT be renamed
    by its member category's label-only rename."""
    env.w(env.envelopes_path, [dict(_ENV_FOOD, name="Fun money")])
    env.w(env.assign_path, {})
    env.set_txns([])
    categories_service.rename_category("cat_13095ba70000", new_label="Groceries")
    assert envelopes_service.envelope_store.find_by_id("env_food0000001")["name"] == "Fun money"


def test_label_only_rename_never_touches_a_merged_envelope(env):
    merged = dict(
        _ENV_FOOD,
        id="env_merged001",
        name="Food",
        category_ids=["cat_13095ba70000", "cat_dfcf9f910000"],
    )
    env.w(env.envelopes_path, [merged])
    env.w(env.assign_path, {})
    env.set_txns([])
    categories_service.rename_category("cat_13095ba70000", new_label="Groceries")
    assert envelopes_service.envelope_store.find_by_id("env_merged001")["name"] == "Food"
