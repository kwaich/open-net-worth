"""
Regression tests for opaque-id category behavior (services.categories).

Covers the opaque-id invariants, end to end:
  * resolve_category_ref resolves a label OR an id (case-insensitive exact match), None for
    unknown
  * create_category mints an opaque id with NO slug derivation from the label
  * rename_category is label-only — a relabel NEVER touches a transaction's stored category
  * rename_category(merge_into=...) folds one category's data (transactions, split lines,
    the legacy budget cap, group membership, Automations) into another and deletes the source

CSV-import label resolution is covered in tests/test_import_csv_service.py; commitment
envelope-id sync is covered in tests/test_sync_commitment_categories.py; vest/option
income booking is covered in tests/test_equity_service.py and tests/test_options_edge.py.

Fully isolated + offline — every store is monkeypatched into tmp_path.
"""

from __future__ import annotations

import pytest

from backend.core.errors import ConflictError, NotFoundError, ValidationError
from backend.db.automation_store import AutomationStore
from backend.db.category_store import CategoryStore
from backend.db.profile_store import ProfileStore
from backend.services import categories as cat


@pytest.fixture
def env(db_isolated):
    # categories/category_groups/budget_assignments/automations/profile are all DB-backed
    # now — db_isolated already gives every one of them a fresh table; ProfileStore starts
    # at {} (no base_currency override needed by these tests).
    ProfileStore().set_raw({})


# ── resolve_category_ref ─────────────────────────────────────────────────────────────


def test_resolve_category_ref_by_id(env):
    created = cat.create_category(label="Groceries")
    assert cat.resolve_category_ref(created["id"]) == created["id"]


def test_resolve_category_ref_by_label_case_insensitive(env):
    created = cat.create_category(label="Groceries")
    assert cat.resolve_category_ref("groceries") == created["id"]
    assert cat.resolve_category_ref("GROCERIES") == created["id"]
    assert cat.resolve_category_ref("  Groceries  ".strip()) == created["id"]


def test_resolve_category_ref_unknown_returns_none(env):
    assert cat.resolve_category_ref("does-not-exist") is None
    assert cat.resolve_category_ref("") is None
    assert cat.resolve_category_ref(None) is None


def test_resolve_category_ref_split_sentinel(env):
    CategoryStore().insert({"id": "split", "label": "Split", "kind": "expense", "color": ""})
    assert cat.resolve_category_ref("split") == "split"


def test_resolve_category_ref_uses_preloaded_rows(env):
    """The `rows=` param lets a bulk caller (CSV/QIF import, bulk_create_transactions) pass
    a pre-loaded CategoryStore().all() once instead of re-reading the store per item."""
    created = cat.create_category(label="Groceries")
    preloaded = cat.all_category_rows()
    CategoryStore().delete(created["id"])  # the live store no longer has it
    # Resolution against the STALE preloaded snapshot still succeeds (proves rows= is used).
    assert cat.resolve_category_ref("groceries", rows=preloaded) == created["id"]
    # ...but a fresh (unpassed) resolution correctly sees it's gone.
    assert cat.resolve_category_ref("groceries") is None


# ── create_category: opaque, no slug derivation ──────────────────────────────────────


def test_create_category_mints_opaque_id_not_a_slug(env):
    result = cat.create_category(label="Side Hustle & Consulting!!")
    assert result["ok"] is True
    assert result["id"].startswith("cat_")
    assert result["id"] != "side_hustle_consulting"  # no slug derivation from the label
    assert result["label"] == "Side Hustle & Consulting!!"


def test_create_category_rejects_duplicate_labels(env):
    """Labels are a flat user-facing namespace — a duplicate (case-insensitive) is refused with
    a ConflictError, so no two categories can share a name."""
    cat.create_category(label="Startup")
    with pytest.raises(ConflictError):
        cat.create_category(label="Startup")
    with pytest.raises(ConflictError):
        cat.create_category(label="  startup ")
    assert len(CategoryStore().all()) == 1


def test_create_category_rejects_blank_label(env):
    with pytest.raises(ValidationError):
        cat.create_category(label="")


def test_create_category_rejects_bad_kind(env):
    with pytest.raises(ValidationError):
        cat.create_category(label="Startup", kind="bogus")


# ── rename_category: relabel is label-only (never re-keys) ──────────────────────────


def test_rename_category_relabel_never_touches_transactions(env):
    from tests.conftest import seed_stub_accounts

    created = cat.create_category(label="Startup")
    seed_stub_accounts([{"from_account_id": "acc"}])
    cat.transaction_store.insert(
        {
            "id": "t1",
            "date": "2026-01-01",
            "kind": "expense",
            "category": created["id"],
            "from_account_id": "acc",
            "from_amount": 10.0,
            "from_currency": "SGD",
        }
    )
    before = cat.transaction_store.all()

    result = cat.rename_category(created["id"], new_label="Venture")
    assert result == {"ok": True, "category_id": created["id"], "label": "Venture", "merged": False}

    after = cat.transaction_store.all()
    assert after == before  # byte-identical — the id never changed, nothing to re-category_id
    row = CategoryStore().get(created["id"])
    assert row["id"] == created["id"] and row["label"] == "Venture"


def test_rename_category_relabel_requires_new_label(env):
    created = cat.create_category(label="Startup")
    with pytest.raises(ValidationError):
        cat.rename_category(created["id"])


def test_rename_category_structural_split_blocked(env):
    CategoryStore().insert({"id": "split", "label": "Split", "kind": "expense", "color": ""})
    with pytest.raises(ValidationError):
        cat.rename_category("split", new_label="Divided")


def test_rename_category_unknown_key_raises(env):
    with pytest.raises(NotFoundError):
        cat.rename_category("cat_doesnotexist", new_label="Anything")


# ── rename_category: merge folds and deletes the source ─────────────────────────────


def test_rename_category_merge_folds_every_reference(env):
    from tests.conftest import seed_stub_accounts

    source = cat.create_category(label="Groceries")
    dest = cat.create_category(label="Food & Dining")

    seed_stub_accounts([{"from_account_id": "acc"}])
    cat.transaction_store.insert(
        {
            "id": "t1",
            "date": "2026-01-01",
            "kind": "expense",
            "category": source["id"],
            "from_account_id": "acc",
            "from_amount": 10.0,
            "from_currency": "SGD",
        }
    )
    cat.transaction_store.insert(
        {
            "id": "t2",
            "date": "2026-01-02",
            "kind": "expense",
            "category": "split",
            "from_account_id": "acc",
            "from_amount": 20.0,
            "from_currency": "SGD",
            "splits": [{"category": source["id"], "amount": 20.0, "description": ""}],
        }
    )
    from backend.services import budgets as budgets_service

    budgets_service.write_budgets({source["id"]: {"limit": 100.0, "rollover": False}})
    AutomationStore().create(
        {
            "id": "automation_1",
            "name": "X category",
            "status": "active",
            "trigger": {
                "kind": "event",
                "event": "txn",
                "when": {"description_contains": "x"},
                "limit": "each",
            },
            # The migrated shape: the category id lives in the instructions PROSE, which is
            # what a merge has to follow now (services/categories._fold_automations).
            "instructions": f'Categorize matching rows.\n{{"category": "{source["id"]}"}}',
            "execution": {"kind": "lucius"},
        }
    )
    result = cat.rename_category(source["id"], merge_into=dest["id"])
    assert result["ok"] is True and result["merged"] is True
    assert result["merged_into"] == dest["id"]
    assert result["counts"]["transactions"] == 2
    assert result["counts"]["budgets"] == 1
    assert "groups" not in result["counts"]
    assert result["counts"]["automations"] == 1

    txns = {t["id"]: t for t in cat.transaction_store.all()}
    assert txns["t1"]["category"] == dest["id"]
    assert txns["t2"]["splits"][0]["category"] == dest["id"]
    assert txns["t2"]["category"] == "split"  # the sentinel itself is untouched

    assert CategoryStore().get(source["id"]) is None
    assert CategoryStore().get(dest["id"]) is not None

    budgets = budgets_service.read_budgets()
    assert source["id"] not in budgets and dest["id"] in budgets

    updated_automation = AutomationStore().find_by_id("automation_1")
    assert dest["id"] in updated_automation["instructions"]
    assert source["id"] not in updated_automation["instructions"]


def test_rename_category_merge_into_self_blocked(env):
    created = cat.create_category(label="Startup")
    with pytest.raises(ValidationError):
        cat.rename_category(created["id"], merge_into=created["id"])


def test_rename_category_merge_unknown_destination_raises(env):
    created = cat.create_category(label="Startup")
    with pytest.raises(NotFoundError):
        cat.rename_category(created["id"], merge_into="cat_doesnotexist")


def test_rename_category_merge_into_structural_split_blocked(env):
    CategoryStore().insert({"id": "split", "label": "Split", "kind": "expense", "color": ""})
    created = cat.create_category(label="Startup")
    with pytest.raises(ValidationError):
        cat.rename_category(created["id"], merge_into="split")


# ── Code-review hardening: resolver ambiguity (2026-07-05) ──────────


def test_resolve_category_ref_ambiguous_label_returns_none(env):
    # Labels are not unique — an ambiguous label must NOT silently resolve to whichever row
    # sorts first (that could book income under an expense-kind category → wrong taxability).
    CategoryStore().bulk_insert(
        [
            {"id": "cat_a1", "label": "Consulting", "kind": "income", "color": ""},
            {"id": "cat_b2", "label": "Consulting", "kind": "expense", "color": ""},
        ]
    )
    assert cat.resolve_category_ref("cat_a1") == "cat_a1"  # exact id still resolves
    assert cat.resolve_category_ref("Consulting") is None  # ambiguous label → None
    assert cat.resolve_category_ref("consulting") is None


def test_resolve_category_ref_unique_label_resolves(env):
    CategoryStore().bulk_insert(
        [{"id": "cat_x", "label": "Groceries", "kind": "expense", "color": ""}]
    )
    assert cat.resolve_category_ref("Groceries") == "cat_x"
    assert cat.resolve_category_ref("GROCERIES") == "cat_x"
    assert cat.resolve_category_ref("cat_x") == "cat_x"
    assert cat.resolve_category_ref("nope") is None
