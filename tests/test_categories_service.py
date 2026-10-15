"""
Unit tests for services.categories — category create / relabel / merge / delete under the
OPAQUE-ID contract (docs/adr/2026-07-05-category-opaque-ids.md).

A category is an immutable ``cat_<12hex>`` id (built-ins carry a stable hardcoded id from
``models.category_defs.BUILTIN_CATEGORIES``); ``label`` is the only human-readable field and is
NEVER derived into/from an id. Relabeling never re-keys anything (the id never changes); only an
explicit merge (``rename_category(category_id, merge_into=...)``) folds one category's data into another
and deletes the loser's row.

Fully isolated + offline: every store the service touches (transactions, rules, assignments,
groups, categories, envelopes) plus the profile path (monthly_budgets / category_targets) is
monkeypatched into a pytest tmp_path.
"""

from __future__ import annotations

import json
import uuid

import pytest

from backend.core.errors import ConflictError, NotFoundError, ValidationError
from backend.db.automation_store import AutomationStore
from backend.db.category_store import CategoryStore
from backend.models.category_defs import BUILTIN_CATEGORIES
from backend.services import categories as cat
from backend.services import envelopes as envelopes_service


class _TxnsProxy:
    """Drop-in replacement for the old ``txns_path`` (a bare ``Path``) now that
    transaction_store is DB-backed (plan 3 task 4) — ``env.txns.write_text(json_str,
    encoding=...)``/``env.txns.read_text()`` keep working exactly as every existing test
    body already calls them, just routed through the real store's ``replace_all``/``all``
    (wipe + reinsert = the same "this IS the whole ledger" semantics a raw full-file
    overwrite had) instead of a JSON file."""

    def write_text(self, data: str, encoding: str = "utf-8") -> None:
        del encoding
        cat.transaction_store.replace_all(json.loads(data))

    def read_text(self) -> str:
        return json.dumps(cat.transaction_store.all())


class _StoreProxy:
    """Same shape as ``_TxnsProxy``, generalised: category_groups/envelopes are ALSO
    DB-backed now (plan 4a task 2) — ``env.groups``/``env.envelopes`` keep working exactly
    as every existing test body already calls them (``.write_text``/``.read_text``), routed
    through the given store's ``replace_all``/``all`` instead of a JSON file."""

    def __init__(self, store):
        self._store = store

    def write_text(self, data: str, encoding: str = "utf-8") -> None:
        del encoding
        self._store.replace_all(json.loads(data))

    def read_text(self) -> str:
        return json.dumps(self._store.all())


class _AssignProxy:
    """Same shape as ``_StoreProxy``, adapted for ``BudgetAssignmentsStore``'s nested
    ``{month: {category: amount}}`` shape (plan 4a task 4 — DB-backed, no bulk
    ``replace_all``; ``write_text`` mirrors ``replace_all``'s wipe+insert semantics via the
    store's own real read/write API — exact-zero is this store's own delete)."""

    def __init__(self, store):
        self._store = store

    def write_text(self, data: str, encoding: str = "utf-8") -> None:
        del encoding
        for month, cats in self._store.all_map().items():
            for category in cats:
                self._store.set_assigned(month, category, 0.0)
        for month, cats in json.loads(data).items():
            for category, amount in cats.items():
                self._store.set_assigned(month, category, amount)

    def read_text(self) -> str:
        return json.dumps(self._store.all_map())


@pytest.fixture
def env(db_isolated):
    from backend.db.profile_store import ProfileStore

    # profile is DB-backed now (Wave 5, JSON->DB) — budgets_service has no more
    # PROFILE_JSON module attr to monkeypatch; seed via ProfileStore directly.
    ProfileStore().set_raw({"base_currency": "SGD"})
    # category_groups/envelopes/budget_assignments/automations are ALL DB-backed now
    # (plan 4a tasks 2 and 4; automations plan 4d task 2) and start empty in the fresh
    # db_isolated database — no explicit wipe/seed needed (unlike profile above,
    # which still needs an explicit row to exist first).

    # Seed the store (mirrors main.py lifespan seed — includes all built-ins + split).
    # CategoryStore is DB-backed too; db_isolated redirected the engine BEFORE this line runs,
    # so this lands in the fresh isolated DB, not the shared default one tests/conftest.py's
    # own pre-seed already (fruitlessly, for THIS test) targeted.
    CategoryStore().bulk_insert(BUILTIN_CATEGORIES)

    def _profile():
        return ProfileStore().get_raw()

    def _set_profile(**keys):
        store = ProfileStore()
        cur = store.get_raw()
        cur.update(keys)
        store.set_raw(cur)

    def _store():
        return CategoryStore()

    def _link_envelope(category_id, name=None):
        """Mint a real 1:1 envelope for `category_id` — mirrors what a live book has for any category
        that's actually budgetable (create_category itself mints no envelope)."""
        envelope = envelopes_service.create_envelope(name or category_id)
        envelopes_service.add_category(envelope["id"], category_id)
        return envelope["id"]

    return type(
        "Env",
        (),
        {
            "txns": _TxnsProxy(),
            "rules": _StoreProxy(AutomationStore()),
            "assign": _AssignProxy(envelopes_service.assignments_store),
            "groups": _StoreProxy(cat.groups_store),
            "envelopes": _StoreProxy(envelopes_service.envelope_store),
            "profile": staticmethod(_profile),
            "set_profile": staticmethod(_set_profile),
            "store": staticmethod(_store),
            "link_envelope": staticmethod(_link_envelope),
        },
    )


# ── Create ────────────────────────────────────────────────────────────────────────


def test_create_category(env):
    out = cat.create_category("Startup Costs", "expense")
    assert out["ok"] is True
    assert out["id"].startswith("cat_")
    assert out["label"] == "Startup Costs"
    assert out["kind"] == "expense"
    row = env.store().get(out["id"])
    assert row is not None and row["label"] == "Startup Costs"


def test_create_default_kind_is_expense(env):
    out = cat.create_category("Foo")
    assert out["kind"] == "expense"


def test_create_with_color(env):
    out = cat.create_category("Gift Income", "income", color="#112233")
    assert out["color"] == "#112233"
    row = env.store().get(out["id"])
    assert row["color"] == "#112233"


def test_create_rejects_duplicate_label(env):
    """Category labels are a flat, user-facing namespace — two same-named categories are
    ambiguous (and would make resolve_category_ref unresolvable), so a duplicate is refused
    with a ConflictError (409). Enforcement is case-insensitive and trims whitespace, and it
    is GLOBAL (not scoped by kind)."""
    cat.create_category("Consulting", "expense")
    with pytest.raises(ConflictError):
        cat.create_category("Consulting", "expense")
    # Case-insensitive + whitespace-insensitive.
    with pytest.raises(ConflictError):
        cat.create_category("  consulting ", "expense")
    # Global across kinds — a same-named income category is refused too.
    with pytest.raises(ConflictError):
        cat.create_category("Consulting", "income")
    # A built-in seed's label is also taken.
    with pytest.raises(ConflictError):
        cat.create_category("food & dining", "expense")


def test_rename_rejects_duplicate_label(env):
    """Relabeling a category onto an existing category's label is refused; relabeling a
    category to its OWN current label (a no-op) is allowed — the row excludes itself."""
    a = cat.create_category("Alpha", "expense")
    b = cat.create_category("Beta", "expense")
    with pytest.raises(ConflictError):
        cat.rename_category(a["id"], new_label="Beta")
    with pytest.raises(ConflictError):
        cat.rename_category(a["id"], new_label="  beta ")
    # Self-relabel (same label, different case) is a no-op, never a self-clash.
    out = cat.rename_category(a["id"], new_label="alpha")
    assert out["ok"] is True and out["label"] == "alpha"
    assert env.store().get(b["id"])["label"] == "Beta"


def test_create_rejects_blank_label(env):
    with pytest.raises(ValidationError):
        cat.create_category("")
    with pytest.raises(ValidationError):
        cat.create_category("   ")


def test_create_rejects_bad_kind(env):
    with pytest.raises(ValidationError):
        cat.create_category("Bad Kind Cat", "notarealkind")


def test_create_never_creates_or_fills_a_group(env):
    before = json.loads(env.groups.read_text())
    for kind in ("expense", "income", "transfer"):
        cat.create_category(f"Thing {kind}", kind)
    after = json.loads(env.groups.read_text())
    assert after == before
    assert not any("categories" in g for g in after)


def test_create_has_no_group_parameter(env):
    with pytest.raises(TypeError):
        cat.create_category("Startup Costs", "expense", group="Biz")  # type: ignore[call-arg]
    assert json.loads(env.groups.read_text()) == []


# ── resolve_category_ref ────────────────────────────────────────────────────────────


def test_resolve_category_ref_by_id(env):
    assert cat.resolve_category_ref("cat_13095ba70000") == "cat_13095ba70000"


def test_resolve_category_ref_by_label_case_insensitive(env):
    assert cat.resolve_category_ref("food & dining") == "cat_13095ba70000"


def test_resolve_category_ref_unknown_returns_none(env):
    assert cat.resolve_category_ref("nonexistent") is None


def test_resolve_category_ref_blank_returns_none(env):
    assert cat.resolve_category_ref("") is None
    assert cat.resolve_category_ref(None) is None


def test_resolve_category_ref_with_prefetched_rows(env):
    rows = cat.all_category_rows()
    assert cat.resolve_category_ref("Transport", rows=rows) == "cat_db5642ab0000"


# ── Rename: relabel branch (pure label update — the id never changes) ─────────────


def _seed_referenced(env, label="Startup"):
    """Create a category referenced in every real place a merge touches — a real 1:1
    envelope (money is envelope-id native, docs/adr/2026-07-05-envelope-id-native.md) plus
    the ledger/monthly_budgets/rules references. Returns (category_id, env_id)."""
    out = cat.create_category(label, "expense")
    category_id = out["id"]
    env_id = env.link_envelope(category_id, label)
    env.txns.write_text(
        json.dumps(
            [
                {
                    "id": "t1",
                    "kind": "expense",
                    "date": "2026-06-01",
                    "category": category_id,
                    "from_amount": 100,
                    "from_currency": "SGD",
                }
            ]
        ),
        encoding="utf-8",
    )
    env.set_profile(
        monthly_budgets={category_id: {"limit": 200.0, "rollover": False}},
        category_targets={env_id: {"type": "monthly", "amount": 150.0}},
    )
    env.assign.write_text(json.dumps({"2026-06": {env_id: 80.0}}), encoding="utf-8")
    env.rules.write_text(json.dumps([_automation_row("r1", category_id)]), encoding="utf-8")
    return category_id, env_id


def test_rename_relabel_does_not_touch_other_stores(env):
    """A relabel only ever updates the row's own label — the id is permanent, so nothing
    else (ledger, budgets, rules) needs to be re-keyed."""
    category_id, env_id = _seed_referenced(env, "Startup")
    out = cat.rename_category(category_id, new_label="Startup Costs")
    assert out == {
        "ok": True,
        "category_id": category_id,
        "label": "Startup Costs",
        "merged": False,
    }

    txns = json.loads(env.txns.read_text())
    assert txns[0]["category"] == category_id
    assert txns[0]["from_amount"] == 100

    prof = env.profile()
    assert category_id in prof["monthly_budgets"]
    assert prof["monthly_budgets"][category_id]["limit"] == 200.0
    assert prof["category_targets"][env_id]["amount"] == 150.0

    rules = json.loads(env.rules.read_text())
    assert category_id in rules[0]["instructions"]

    row = env.store().get(category_id)
    assert row["label"] == "Startup Costs"


def test_rename_relabel_envelope_name_follows_1to1(env):
    out = cat.create_category("Old Label", "expense")
    category_id = out["id"]
    env_id = env.link_envelope(category_id, "Old Label")
    cat.rename_category(category_id, new_label="New Label")
    envelope = next(e for e in json.loads(env.envelopes.read_text()) if e["id"] == env_id)
    assert envelope["name"] == "New Label"


def test_rename_relabel_requires_new_label(env):
    out = cat.create_category("Startup")
    with pytest.raises(ValidationError):
        cat.rename_category(out["id"])


def test_rename_relabel_blank_label_raises(env):
    out = cat.create_category("Startup")
    with pytest.raises(ValidationError):
        cat.rename_category(out["id"], new_label="   ")


def test_rename_rejects_structural_key(env):
    with pytest.raises(ValidationError):
        cat.rename_category("split", new_label="Divided")


def test_rename_missing_key_raises_notfound(env):
    with pytest.raises(NotFoundError):
        cat.rename_category("cat_ghost", new_label="Phantom")


# ── Rename: merge branch (the atomic cross-store fold) ────────────────────────────


def test_rename_merge_into_missing_raises_notfound(env):
    out = cat.create_category("Startup")
    with pytest.raises(NotFoundError):
        cat.rename_category(out["id"], merge_into="cat_ghost")


def test_rename_merge_into_self_raises_validation(env):
    out = cat.create_category("Startup")
    with pytest.raises(ValidationError):
        cat.rename_category(out["id"], merge_into=out["id"])


def test_rename_merge_into_structural_raises_validation(env):
    out = cat.create_category("Startup")
    with pytest.raises(ValidationError):
        cat.rename_category(out["id"], merge_into="split")


def test_rename_merge_without_envelope_still_folds_ledger_data(env):
    category_id = cat.create_category("Startup")["id"]
    venture_id = cat.create_category("Venture")["id"]
    env.txns.write_text(
        json.dumps(
            [
                {
                    "id": "t1",
                    "kind": "expense",
                    "date": "2026-06-01",
                    "category": category_id,
                    "from_amount": 50,
                    "from_currency": "SGD",
                }
            ]
        ),
        encoding="utf-8",
    )

    result = cat.rename_category(category_id, merge_into=venture_id)
    assert result["merged"] is True
    assert result["merged_into"] == venture_id
    assert result["counts"]["transactions"] == 1
    assert result["counts"]["envelopes"] == 0  # neither category was linked to an envelope

    txns = json.loads(env.txns.read_text())
    assert txns[0]["category"] == venture_id
    assert env.store().get(category_id) is None
    assert env.store().get(venture_id) is not None


def test_rename_merge_folds_every_reference(env):
    category_id, env_id = _seed_referenced(env, "Startup")
    venture_id = cat.create_category("Venture", "expense")["id"]
    venture_env_id = env.link_envelope(venture_id, "Venture")
    env.assign.write_text(
        json.dumps({"2026-06": {env_id: 80.0, venture_env_id: 20.0}}), encoding="utf-8"
    )

    out = cat.rename_category(category_id, merge_into=venture_id)
    assert out["ok"] is True
    assert out["merged"] is True
    assert out["category_id"] == category_id
    assert out["merged_into"] == venture_id
    assert out["counts"] == {
        "transactions": 1,
        "budgets": 1,
        "automations": 1,
        "envelopes": 2,  # startup's whole (sole-member) envelope folds into venture's
    }

    txns = json.loads(env.txns.read_text())
    assert txns[0]["category"] == venture_id
    assert txns[0]["from_amount"] == 100

    prof = env.profile()
    assert venture_id in prof["monthly_budgets"] and category_id not in prof["monthly_budgets"]
    assert prof["monthly_budgets"][venture_id]["limit"] == 200.0
    # The dying envelope's OWN target dies with it (never moved) — mirrors delete_envelope.
    assert env_id not in prof["category_targets"]

    store = env.store()
    assert store.get(category_id) is None
    assert store.get(venture_id) is not None

    # The merge follows the id into the instructions PROSE, where the migration put it.
    rules = json.loads(env.rules.read_text())
    assert venture_id in rules[0]["instructions"]
    assert category_id not in rules[0]["instructions"]

    assign = json.loads(env.assign.read_text())["2026-06"]
    assert assign[venture_env_id] == 100.0
    assert assign.get(env_id, 0.0) == 0.0

    envelopes = {e["id"]: e for e in json.loads(env.envelopes.read_text())}
    # Hard-deleted for real (ADR 2026-09-26-deleting-an-envelope-deletes-it) — no more archive.
    assert env_id not in envelopes
    assert envelopes[venture_env_id]["archived"] is False


# ── Delete (in-use safe) ──────────────────────────────────────────────────────────


def test_delete_unused(env):
    out = cat.create_category("Startup")
    result = cat.delete_category(out["id"])
    assert result["ok"] and result["reassigned"] == 0
    assert env.store().get(out["id"]) is None


def test_delete_in_use_refused(env):
    category_id, _ = _seed_referenced(env, "Startup")
    with pytest.raises(ConflictError):
        cat.delete_category(category_id)
    assert json.loads(env.txns.read_text())[0]["category"] == category_id
    assert env.store().get(category_id) is not None


def test_delete_in_use_reassign(env):
    category_id, env_id = _seed_referenced(env, "Startup")
    out = cat.delete_category(category_id, reassign=True)
    assert out["ok"] and out["reassigned"] == 1
    assert json.loads(env.txns.read_text())[0]["category"] is None
    assert out["reassigned_to"] is None
    prof = env.profile()
    assert category_id not in prof["monthly_budgets"]
    assert env_id not in prof["category_targets"]
    assert env.store().get(category_id) is None
    # The Automation is PAUSED, not deleted: its match conditions and instructions are the
    # owner's work and worth keeping to edit. Leaving it active would fail on every match.
    rules = json.loads(env.rules.read_text())
    assert [r["status"] for r in rules] == ["paused"]
    assert json.loads(env.assign.read_text()).get("2026-06", {}).get(env_id) in (None, 0.0)
    # Hard-deleted for real (ADR 2026-09-26-deleting-an-envelope-deletes-it) — no more archive.
    envelope = next((e for e in json.loads(env.envelopes.read_text()) if e["id"] == env_id), None)
    assert envelope is None


def test_deleting_a_category_clears_the_payee_defaults_that_named_it(env):
    """A payee default naming a deleted category pointed nowhere and made the payee's next
    uncategorized transaction fail "Unknown category" (audit, 2026-09-26)."""
    from backend.services import payee_registry

    category_id = cat.create_category("Startup", "expense")["id"]
    payee = payee_registry.create_payee("Acme Studio", default_category=category_id)
    cat.delete_category(category_id)
    from backend.db.payee_store import PayeeStore

    stored = PayeeStore().find_by_id(payee["id"]) or {}
    assert (stored.get("default_categories") or {}).get("expense") is None


def test_deleting_with_reassign_moves_the_payee_default_where_the_transactions_went(env):
    from backend.services import payee_registry

    category_id, _ = _seed_referenced(env, "Startup")
    venture_id = cat.create_category("Venture")["id"]
    payee = payee_registry.create_payee("Acme Studio", default_category=category_id)
    cat.delete_category(category_id, reassign=True, reassign_to=venture_id)
    from backend.db.payee_store import PayeeStore

    stored = PayeeStore().find_by_id(payee["id"]) or {}
    assert (stored.get("default_categories") or {}).get("expense") == venture_id


def test_delete_in_use_reassign_to_explicit_target(env):
    category_id, _ = _seed_referenced(env, "Startup")
    venture_id = cat.create_category("Venture")["id"]
    out = cat.delete_category(category_id, reassign=True, reassign_to=venture_id)
    assert out["ok"] and out["reassigned"] == 1 and out["reassigned_to"] == venture_id
    assert json.loads(env.txns.read_text())[0]["category"] == venture_id


def test_delete_reassign_to_unknown_target_refused(env):
    category_id, _ = _seed_referenced(env, "Startup")
    with pytest.raises(ValidationError):
        cat.delete_category(category_id, reassign=True, reassign_to="cat_ghost")


def _seed_split_referenced(env, label="Hobby"):
    """A split transaction whose top-level category is the ``split`` sentinel and whose only
    reference to the new category lives inside a split LINE (the regression path). Returns
    the new category's id."""
    category_id = cat.create_category(label, "expense")["id"]
    env.txns.write_text(
        json.dumps(
            [
                {
                    "id": "t1",
                    "kind": "expense",
                    "date": "2026-06-01",
                    "category": "split",
                    "from_amount": 100,
                    "from_currency": "SGD",
                    "splits": [
                        {"category": "cat_13095ba70000", "amount": 60.0, "note": ""},
                        {"category": category_id, "amount": 40.0, "note": ""},
                    ],
                }
            ]
        ),
        encoding="utf-8",
    )
    return category_id


def test_category_in_use_counts_split_lines(env):
    """Regression: a category used ONLY inside split lines is in-use (top-level is 'split')."""
    category_id = _seed_split_referenced(env)
    assert cat.category_in_use(category_id) == 1


def test_delete_split_line_only_category_refused_without_reassign(env):
    """Regression (blocker): deleting a split-line-only category without reassign must REFUSE,
    not silently drop the row and orphan the split money out of every budget total."""
    category_id = _seed_split_referenced(env)
    with pytest.raises(ConflictError):
        cat.delete_category(category_id)
    assert env.store().get(category_id) is not None
    assert json.loads(env.txns.read_text())[0]["splits"][1]["category"] == category_id


def test_delete_split_line_only_category_reassign_clears_line(env):
    """Regression (blocker): with reassign, the split LINE is cleared to None (→ Uncategorized),
    never left dangling on a deleted category id."""
    category_id = _seed_split_referenced(env)
    out = cat.delete_category(category_id, reassign=True)
    assert out["ok"] and out["reassigned"] == 1 and out["reassigned_to"] is None
    txn = json.loads(env.txns.read_text())[0]
    assert txn["splits"][1]["category"] is None
    assert txn["splits"][0]["category"] == "cat_13095ba70000"
    assert env.store().get(category_id) is None


def test_full_scrub_of_a_directly_inserted_legacy_category(env):
    """delete_category must fully scrub ANY category row — including one that was never
    created via create_category (e.g. a legacy row surviving a data migration) — across
    every store it references: top-level txn, split line, budget, target, assignment,
    group, and a category-only rule."""
    CategoryStore().bulk_insert(
        [{"id": "cat_legacy", "label": "Legacy Uncategorized", "kind": "expense", "color": "#889"}]
    )
    env_id = env.link_envelope("cat_legacy", "Legacy Uncategorized")
    env.txns.write_text(
        json.dumps(
            [
                {
                    "id": "t1",
                    "kind": "income",
                    "date": "2026-06-01",
                    "category": "cat_legacy",
                    "from_amount": 50,
                    "from_currency": "SGD",
                },
                {
                    "id": "t2",
                    "kind": "expense",
                    "date": "2026-06-02",
                    "category": "split",
                    "from_amount": 100,
                    "from_currency": "SGD",
                    "splits": [
                        {"category": "cat_13095ba70000", "amount": 60.0, "note": ""},
                        {"category": "cat_legacy", "amount": 40.0, "note": ""},
                    ],
                },
            ]
        ),
        encoding="utf-8",
    )
    env.set_profile(
        monthly_budgets={"cat_legacy": {"limit": 10.0, "rollover": False}},
        category_targets={env_id: {"type": "monthly", "amount": 5.0}},
    )
    env.assign.write_text(json.dumps({"2026-06": {env_id: 7.0}}), encoding="utf-8")
    env.rules.write_text(
        json.dumps([_automation_row("r1", "cat_legacy", stop=False)]), encoding="utf-8"
    )

    out = cat.delete_category("cat_legacy", reassign=True, reassign_to=None)
    assert out["ok"] and out["reassigned_to"] is None

    txns = json.loads(env.txns.read_text())
    assert txns[0]["category"] is None
    assert txns[1]["splits"][1]["category"] is None
    assert txns[1]["splits"][0]["category"] == "cat_13095ba70000"
    prof = env.profile()
    assert "cat_legacy" not in prof["monthly_budgets"]
    assert env_id not in prof["category_targets"]
    assert json.loads(env.assign.read_text()).get("2026-06", {}).get(env_id) in (None, 0.0)
    # The Automation is PAUSED, not deleted: its match conditions and instructions are the
    # owner's work and worth keeping to edit. Leaving it active would fail on every match.
    rules = json.loads(env.rules.read_text())
    assert [r["status"] for r in rules] == ["paused"]
    assert env.store().get("cat_legacy") is None
    # Hard-deleted for real (ADR 2026-09-26-deleting-an-envelope-deletes-it) — no more archive.
    envelope = next((e for e in json.loads(env.envelopes.read_text()) if e["id"] == env_id), None)
    assert envelope is None


# ── Deleting former built-in SPENDING categories ──────────────────────────────────
# After the opaque-id migration: deletion removes the store row — genuinely gone, never
# resurrected.


def test_delete_builtin_spending_unused(env):
    """A former built-in (cat_13095ba70000) with no transactions deletes by removing the store row."""
    out = cat.delete_category("cat_13095ba70000")
    assert out["ok"] and out["reassigned"] == 0
    assert env.store().get("cat_13095ba70000") is None
    assert "cat_13095ba70000" not in cat._all_ids()


def test_delete_builtin_spending_in_use_refused_without_reassign(env):
    env.txns.write_text(
        json.dumps(
            [
                {
                    "id": "t1",
                    "kind": "expense",
                    "date": "2026-06-01",
                    "category": "cat_13095ba70000",
                    "from_amount": 100,
                    "from_currency": "SGD",
                }
            ]
        ),
        encoding="utf-8",
    )
    with pytest.raises(ConflictError):
        cat.delete_category("cat_13095ba70000")
    assert json.loads(env.txns.read_text())[0]["category"] == "cat_13095ba70000"
    assert env.store().get("cat_13095ba70000") is not None


def test_delete_builtin_spending_in_use_reassign(env):
    env.txns.write_text(
        json.dumps(
            [
                {
                    "id": "t1",
                    "kind": "expense",
                    "date": "2026-06-01",
                    "category": "cat_13095ba70000",
                    "from_amount": 100,
                    "from_currency": "SGD",
                }
            ]
        ),
        encoding="utf-8",
    )
    out = cat.delete_category("cat_13095ba70000", reassign=True)
    assert out["ok"] and out["reassigned"] == 1 and out["reassigned_to"] is None
    assert json.loads(env.txns.read_text())[0]["category"] is None
    assert env.store().get("cat_13095ba70000") is None


def test_delete_structural_key_refused(env):
    with pytest.raises(ValidationError):
        cat.delete_category("split")


def test_delete_grandfathered_adjustment_category(env):
    """The old kind=adjustment seed is gone (adjustment txns always carry category=None), but
    a grandfathered row on an existing book is pure data and fully user-deletable."""
    CategoryStore().bulk_insert(
        [
            {
                "id": "cat_adjustment_legacy",
                "label": "Adjustment",
                "kind": "adjustment",
                "color": "#95a5a6",
            }
        ]
    )
    out = cat.delete_category("cat_adjustment_legacy")
    assert out["ok"]
    assert env.store().get("cat_adjustment_legacy") is None


def test_delete_unknown_id_not_found(env):
    # 'investment' is a transaction kind, never a category id — and never present in the store.
    with pytest.raises(NotFoundError):
        cat.delete_category("investment")


def test_delete_missing(env):
    with pytest.raises(NotFoundError):
        cat.delete_category("cat_ghost")


# ── REGRESSION: delete-then-reboot must NOT resurrect the category ──────────────────


def test_delete_builtin_gone_from_classification(env):
    """Delete 'cat_c19c4c0f0000' → it must be absent from get_all_categories AND from the id set."""
    assert env.store().get("cat_c19c4c0f0000") is not None
    cat.delete_category("cat_c19c4c0f0000")
    assert env.store().get("cat_c19c4c0f0000") is None
    ids = {c["id"] for c in cat.get_all_categories()}
    assert "cat_c19c4c0f0000" not in ids
    assert "cat_c19c4c0f0000" not in cat._all_ids()


def test_delete_builtin_not_resurrected_on_next_instantiation(env):
    """After deleting 'cat_c19c4c0f0000', a fresh CategoryStore() must not see it."""
    cat.delete_category("cat_c19c4c0f0000")
    fresh = CategoryStore()
    assert fresh.get("cat_c19c4c0f0000") is None
    all_ids = {r["id"] for r in fresh.all()}
    assert "cat_c19c4c0f0000" not in all_ids


# ── Delete + hidden interplay ──────────────────────────────────────────────────────


def test_delete_clears_hidden_entry(env):
    """Deleting a hidden category removes the hidden flag (row itself is deleted)."""
    category_id = cat.create_category("Startup")["id"]
    cat.set_category_hidden(category_id, hidden=True)
    assert category_id in cat.hidden_categories()

    cat.delete_category(category_id)
    assert category_id not in cat.hidden_categories()


def test_delete_then_readd_not_hidden(env):
    """hide → delete → re-add: the re-added category must NOT be hidden, and must get a
    freshly-minted id (ids are never reused)."""
    category_id = cat.create_category("Startup")["id"]
    cat.set_category_hidden(category_id, hidden=True)
    cat.delete_category(category_id)
    key2 = cat.create_category("Startup")["id"]
    assert key2 != category_id
    assert key2 not in cat.hidden_categories()


def test_delete_builtin_clears_hidden(env):
    """Deleting a hidden former built-in clears the row (and hidden state)."""
    cat.set_category_hidden("cat_13095ba70000", hidden=True)
    assert "cat_13095ba70000" in cat.hidden_categories()
    cat.delete_category("cat_13095ba70000")
    assert "cat_13095ba70000" not in cat.hidden_categories()


# ── Rule cleanup ───────────────────────────────────────────────────────────────────


def _automation_row(
    automation_id: str,
    category: str,
    *,
    tags: list | None = None,
    note_append: str | None = None,
    stop: bool = True,
) -> dict:
    """A migrated categorization Automation: Lucius execution, with the category id in the
    instructions PROSE where the migration put it (2026-08-02) — there is no structured
    ``execution.set`` any more, which is exactly why delete can no longer rewrite it."""
    extras = "".join(
        [
            f"\nTags to add: {tags}" if tags else "",
            f"\nNote to append: {note_append!r}" if note_append else "",
        ]
    )
    return {
        "id": automation_id,
        "name": "test automation",
        "description": "",
        "instructions": (
            f'Categorize coffee.\n\nTransformation:\n{{\n  "category": "{category}"\n}}{extras}'
        ),
        "status": "active",
        "trigger": {
            "kind": "event",
            "event": "txn",
            "when": {"description_contains": "coffee"},
            "limit": "each",
        },
        "execution": {"kind": "lucius"},
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
        "provenance": {},
        "stats": {},
    }


def _seed_rule(env, *, category: str, tags: list, note_append: str | None = None) -> str:
    rule_id = str(uuid.uuid4())
    env.rules.write_text(
        json.dumps([_automation_row(rule_id, category, tags=tags, note_append=note_append)]),
        encoding="utf-8",
    )
    return rule_id


def test_delete_refuses_while_an_automation_assigns_the_category(env):
    """Delete used to rewrite the rule's structured category field, deleting the rule when
    nothing else remained. Migrated to Lucius, the id lives in the instructions PROSE and
    nothing can rewrite that safely — so an unnoticed delete would leave the Automation
    naming a dead id and failing on every matching transaction from then on.

    It refuses instead, the same way delete_account refuses while transactions reference it.
    """
    from backend.core.errors import ConflictError

    category_id = cat.create_category("Startup")["id"]
    _seed_rule(env, category=category_id, tags=[], note_append=None)

    with pytest.raises(ConflictError, match="Automation"):
        cat.delete_category(category_id)

    assert len(json.loads(env.rules.read_text())) == 1  # untouched


def test_the_refusal_names_the_automations_so_they_can_be_found(env):
    from backend.core.errors import ConflictError

    category_id = cat.create_category("Startup")["id"]
    _seed_rule(env, category=category_id, tags=["biz"], note_append=None)

    with pytest.raises(ConflictError) as excinfo:
        cat.delete_category(category_id)

    assert "test automation" in str(excinfo.value)


def test_delete_proceeds_when_no_automation_assigns_the_category(env):
    category_id = cat.create_category("Startup")["id"]
    other = cat.create_category("Unrelated")["id"]
    _seed_rule(env, category=other, tags=[], note_append=None)

    cat.delete_category(category_id)

    assert env.store().get(category_id) is None  # the delete went through
    rules = json.loads(env.rules.read_text())
    assert [r["status"] for r in rules] == ["active"]  # the other Automation is untouched


def test_reassign_folds_the_id_inside_the_automations_instructions(env):
    """The fold branch had no coverage. Since migration the category id lives in the
    instructions PROSE, so reassigning has to rewrite text — the one place a wrong match
    would silently corrupt an unrelated Automation. Ids are fixed-width `cat_<12hex>`, so an
    exact substring swap is safe; this pins that it actually happens."""
    category_id = cat.create_category("Startup")["id"]
    target = cat.create_category("Business")["id"]
    _seed_rule(env, category=category_id, tags=[], note_append=None)

    cat.delete_category(category_id, reassign=True, reassign_to=target)

    assert env.store().get(category_id) is None
    rules = json.loads(env.rules.read_text())
    assert category_id not in rules[0]["instructions"]
    assert target in rules[0]["instructions"]
    assert rules[0]["status"] == "active"  # folded, not paused


def test_reassign_with_no_target_pauses_the_automations_instead_of_orphaning_them(env):
    """Clearing to "unsorted" leaves no id to fold to. Rather than leave the Automation
    naming a category that no longer exists — failing on every matching transaction, silently
    — it is paused so the owner finds it."""
    category_id = cat.create_category("Startup")["id"]
    _seed_rule(env, category=category_id, tags=[], note_append=None)

    cat.delete_category(category_id, reassign=True)

    assert env.store().get(category_id) is None
    assert json.loads(env.rules.read_text())[0]["status"] == "paused"
