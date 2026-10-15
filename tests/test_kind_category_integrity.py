"""Tests for kind/category collision elimination.

Covers:
- Planning RTA: unclassified income (category=None) is inflow-to-assign (not envelope refund)
- get_transactions AI tool: category='transfer' auto-maps to transaction_type='transfer'
- Category create/rename ALLOWS a kind name as a LABEL now (opaque ids make the old
  collision structurally impossible — see docs/adr/2026-07-05-category-opaque-ids.md);
  the real collision guard lives at the transaction-write choke instead.

Run:
    .venv/bin/pytest tests/test_kind_category_integrity.py -v
"""

from __future__ import annotations

import pytest


# ── planning: unclassified (None) income → RTA inflow, not envelope refund ───────
def test_inflow_uncategorized_sink_removed():
    """The 'uncategorized' sink was removed (categories are optional; None == unclassified).
    It must NOT be in the inflow set — no resurrected back-compat shim. Unclassified income now
    carries category=None (coerced to "") which the empty-string membership below covers, so
    unlabelled income still counts as Ready-to-Assign inflow, never a phantom envelope refund.
    """
    from backend.services.planning import _INFLOW_CATEGORIES

    assert "uncategorized" not in _INFLOW_CATEGORIES
    assert "income" not in _INFLOW_CATEGORIES
    assert "" in _INFLOW_CATEGORIES  # None/unclassified income is the RTA-inflow marker now


def test_inflow_categories_includes_empty_string():
    from backend.services.planning import _INFLOW_CATEGORIES

    assert "" in _INFLOW_CATEGORIES


def test_inflow_categories_includes_ready_to_assign():
    from backend.services.planning import _INFLOW_CATEGORIES

    assert "ready_to_assign" in _INFLOW_CATEGORIES


# ── categories service: kind names refused as category keys ──────────────────────
@pytest.fixture
def cat_env(db_isolated):
    import backend.services.categories as cat_mod
    from backend.db.profile_store import ProfileStore

    # profile is DB-backed now (Wave 5, JSON->DB); budget_assignments/category_groups
    # are DB-backed too (plan 4a) and start empty in the fresh db_isolated database —
    # no explicit wipe/seed needed for either.
    ProfileStore().set_raw({"base_currency": "SGD"})
    return cat_mod


def test_create_category_allows_kind_name_as_label_income(cat_env):
    # Category-opaque-ids migration: ids are minted server-side (cat_<12hex>), so a
    # kind name can never collide with an id anymore — the old "refuse a kind name as
    # the key" guard is gone (structurally impossible). A category's LABEL is just
    # display text and may freely equal a kind name; the real collision guard now lives
    # at the transaction-write choke (services.ledger._validate_reserved_category).
    result = cat_env.create_category("income", kind="income")
    assert result["ok"] is True
    assert result["label"] == "income"


def test_create_category_allows_kind_name_as_label_transfer(cat_env):
    result = cat_env.create_category("transfer", kind="expense")
    assert result["ok"] is True
    assert result["label"] == "transfer"


def test_create_category_allows_kind_name_as_label_expense(cat_env):
    result = cat_env.create_category("expense", kind="expense")
    assert result["ok"] is True
    assert result["label"] == "expense"


def test_create_category_allows_kind_name_as_label_adjustment(cat_env):
    result = cat_env.create_category("adjustment", kind="adjustment")
    assert result["ok"] is True
    assert result["label"] == "adjustment"


def test_rename_category_allows_kind_name_as_new_label(cat_env):
    # Renaming to a kind-name LABEL is likewise allowed now — the id (unaffected by a
    # relabel) is what the collision guard cares about, and a minted id can never equal
    # a kind name.
    created = cat_env.create_category("My Category")
    result = cat_env.rename_category(created["id"], new_label="income")
    assert result["ok"] is True
    assert result["label"] == "income"


# ── get_transactions tool: kind name as category → auto-maps to transaction_type ─
# ── category namespace: income and transfer no longer appear in builtin list ──────
def test_income_transfer_not_in_category_store():
    """income and transfer are transaction kinds, not category keys — must not be in the store."""
    from backend.services.categories import _all_ids

    all_keys = _all_ids()
    assert "income" not in all_keys
    assert "transfer" not in all_keys


def test_income_transfer_in_kind_name_keys():
    """income and transfer are in _KIND_NAME_KEYS — permanently reserved, blocked at create."""
    from backend.services.categories import _KIND_NAME_KEYS

    assert "income" in _KIND_NAME_KEYS
    assert "transfer" in _KIND_NAME_KEYS


def test_income_transfer_not_in_structural_keys():
    from backend.services.categories import STRUCTURAL_KEYS

    assert "income" not in STRUCTURAL_KEYS
    assert "transfer" not in STRUCTURAL_KEYS


def test_category_to_kind_has_no_income_transfer_or_investment_categories():
    from backend.api.ledger import category_to_kind
    from backend.services.categories import _KIND_NAME_KEYS

    # "income", "transfer", and "investment" must not be category ids — they are kinds.
    mapping = category_to_kind()
    assert "income" not in mapping
    assert "transfer" not in mapping
    assert "investment" not in mapping
    # All three are now reserved as kind names.
    assert "investment" in _KIND_NAME_KEYS
    assert "income" in _KIND_NAME_KEYS
    assert "transfer" in _KIND_NAME_KEYS
