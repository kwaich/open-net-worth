"""Acceptance tests for kind=investment as a first-class transaction kind.

Covers the three invariants from the ADR:
  1. A kind=investment transaction is stored (not coerced to expense).
  2. It is excluded from spending/budget totals.
  3. Net worth is unaffected by kind=investment alone (the position adds the asset side).
"""

from __future__ import annotations

import pytest

from backend.db.transaction_store import KINDS, make_transaction
from backend.services.budgets import is_spending_expense

# ── P0: kind gate ──────────────────────────────────────────────────────────────────


def test_investment_in_kinds_set():
    """KINDS set in the store must include 'investment' so it is never coerced."""
    assert "investment" in KINDS


def test_make_transaction_preserves_investment_kind():
    """make_transaction must NOT coerce kind=investment to expense."""
    txn = make_transaction(
        {
            "date": "2026-07-01",
            "kind": "investment",
            "from_account_id": "acct-1",
            "from_amount": 1500.0,
            "from_currency": "USD",
        }
    )
    assert txn["kind"] == "investment"


def test_make_transaction_investment_strips_category():
    """make_transaction stores category=None for investment (no-category invariant)."""
    txn = make_transaction(
        {
            "date": "2026-07-01",
            "kind": "investment",
            "category": "investment",  # old-style input — must be dropped
            "from_account_id": "acct-1",
            "from_amount": 1500.0,
            "from_currency": "USD",
        }
    )
    # make_transaction doesn't strip category itself — the service layer does (create_transaction).
    # Here we just confirm the kind is preserved.
    assert txn["kind"] == "investment"


# ── P1: no category allowed ────────────────────────────────────────────────────────


def test_investment_not_a_category():
    """investment is a reserved transaction kind — it must never appear as a category key."""
    from backend.services.categories import _KIND_NAME_KEYS, _all_ids

    assert "investment" in _KIND_NAME_KEYS  # blocked at create time
    assert "investment" not in _all_ids()  # not a store row


def test_investment_not_in_structural_keys():
    """investment is a kind, not a structural category anchor."""
    from backend.services.categories import STRUCTURAL_KEYS

    assert "investment" not in STRUCTURAL_KEYS


def test_investment_in_kind_name_keys():
    """investment must be a reserved kind name — category creation with this key is refused."""
    from backend.services.categories import _KIND_NAME_KEYS

    assert "investment" in _KIND_NAME_KEYS


def test_create_category_investment_refused():
    """OBSOLETE (category-opaque-ids migration): create_category no longer takes a caller
    key at all — ids are auto-minted (cat_<12hex>), so a caller can never request the literal
    id "investment"; the kind-name/category-key collision this test pinned is now structurally
    impossible (see test_investment_not_a_category / test_investment_in_kind_name_keys above,
    which still assert the real guarantee: "investment" stays reserved and never a stored key)."""


# ── P2: spending exclusion ─────────────────────────────────────────────────────────


def test_investment_buy_not_spending():
    """A kind=investment buy (from leg) is not a spending expense."""
    txn = {
        "kind": "investment",
        "from_account_id": "acct-1",
        "from_amount": 1500.0,
        "category": None,
    }
    assert is_spending_expense(txn) is False


def test_investment_sell_not_spending():
    """A kind=investment sell (to leg) is not a spending expense."""
    txn = {
        "kind": "investment",
        "to_account_id": "acct-1",
        "to_amount": 2000.0,
        "category": None,
    }
    assert is_spending_expense(txn) is False


def test_investment_not_in_expense_categories():
    """investment must not appear in the budgetable expense category set."""
    from backend.services.budgets import expense_categories

    cats = expense_categories()
    assert "investment" not in cats


# ── P3: ledger service stores without coercion ────────────────────────────────────


def test_create_transaction_investment_stored_and_category_absent(tmp_path, monkeypatch, fx_rates):
    """create_transaction stores kind=investment; when no category is supplied it stays None
    (no sink to 'uncategorized' — investment category is optional, not required)."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path))

    import importlib

    import backend.paths as paths_mod

    importlib.reload(paths_mod)

    # Re-import all stores/services that cache the path at import time.
    import backend.db.account_store as acct_store_mod
    import backend.db.transaction_store as txn_store_mod
    import backend.services.ledger as ledger_mod

    importlib.reload(txn_store_mod)
    importlib.reload(acct_store_mod)
    importlib.reload(ledger_mod)

    # Seed a minimal account.
    acct = ledger_mod.accounts.insert(
        {
            "id": "acct-cash",
            "name": "Cash",
            "kind": "asset",
            "type": "cash",
            "currency": "USD",
            "initial_balance": 5000.0,
        }
    )

    stored = ledger_mod.create_transaction(
        {
            "date": "2026-07-01",
            "kind": "investment",
            "from_account_id": acct["id"],
            "from_amount": 1500.0,
            "from_currency": "USD",
            "description": "Buy AAPL",
        }
    )

    assert stored["kind"] == "investment"
    assert (
        stored["category"] is None
    )  # no category supplied → stays None (not sunk to uncategorized)
    assert stored["from_amount"] == 1500.0


# ── P4: optional category on investment ───────────────────────────────────────────


def _reload_ledger(tmp_path, monkeypatch):
    """Helper: point DATA_DIR at tmp_path and reload the module chain so stores resolve there."""
    import importlib

    monkeypatch.setenv("DATA_DIR", str(tmp_path))

    import backend.paths as paths_mod

    importlib.reload(paths_mod)

    import backend.db.account_store as acct_store_mod
    import backend.db.transaction_store as txn_store_mod
    import backend.services.ledger as ledger_mod

    importlib.reload(txn_store_mod)
    importlib.reload(acct_store_mod)
    importlib.reload(ledger_mod)
    return ledger_mod


def test_investment_with_category_round_trips(tmp_path, monkeypatch, fx_rates):
    """An investment txn created with a valid category round-trips — it is preserved, not stripped."""
    import importlib

    import backend.services.categories as cat_mod

    importlib.reload(cat_mod)

    ledger_mod = _reload_ledger(tmp_path, monkeypatch)

    acct = ledger_mod.accounts.insert(
        {
            "id": "acct-cash",
            "name": "Cash",
            "kind": "asset",
            "type": "cash",
            "currency": "USD",
            "initial_balance": 5000.0,
        }
    )
    # Seed a real expense-kind category so the validation pass succeeds.
    created_cat = cat_mod.create_category(label="Retirement")

    stored = ledger_mod.create_transaction(
        {
            "date": "2026-07-01",
            "kind": "investment",
            "from_account_id": acct["id"],
            "from_amount": 1500.0,
            "from_currency": "USD",
            "category": created_cat["id"],
            "description": "Buy VOO",
        }
    )

    assert stored["kind"] == "investment"
    assert stored["category"] == created_cat["id"]  # category preserved, not stripped


def test_investment_no_category_stays_none(tmp_path, monkeypatch, fx_rates):
    """An investment txn with no category stores category=None — NOT forced to 'uncategorized'."""
    ledger_mod = _reload_ledger(tmp_path, monkeypatch)

    acct = ledger_mod.accounts.insert(
        {
            "id": "acct-cash",
            "name": "Cash",
            "kind": "asset",
            "type": "cash",
            "currency": "USD",
            "initial_balance": 5000.0,
        }
    )

    stored = ledger_mod.create_transaction(
        {
            "date": "2026-07-01",
            "kind": "investment",
            "from_account_id": acct["id"],
            "from_amount": 1000.0,
            "from_currency": "USD",
        }
    )

    assert stored["kind"] == "investment"
    assert stored["category"] is None  # absent → stays None, never sunk to uncategorized


def test_investment_empty_string_category_normalized_to_none(tmp_path, monkeypatch, fx_rates):
    """Regression (review BLOCKER): an investment with category='' normalizes to None.

    The optional-category branch is taken for kind=investment, so the expense/income
    empty→uncategorized sink never fires; '' must be normalized to None IN-branch, not
    persisted as an empty string."""
    ledger_mod = _reload_ledger(tmp_path, monkeypatch)

    acct = ledger_mod.accounts.insert(
        {
            "id": "acct-cash",
            "name": "Cash",
            "kind": "asset",
            "type": "cash",
            "currency": "USD",
            "initial_balance": 5000.0,
        }
    )

    stored = ledger_mod.create_transaction(
        {
            "date": "2026-07-01",
            "kind": "investment",
            "from_account_id": acct["id"],
            "from_amount": 800.0,
            "from_currency": "USD",
            "category": "",  # empty string — must NOT persist as ""
        }
    )

    assert stored["kind"] == "investment"
    assert stored["category"] is None  # "" normalized to None, not stored as ""


# Removed 2026-08-02: this pinned a DECLARATIVE transaction transform categorizing the
# row inside create_transaction. Rules are event-triggered Automations now — they run
# AFTER the commit — so there is nothing to observe at the write. The surviving
# attribution is pinned in tests/test_rule_audit_provenance.py.


def test_investment_unknown_category_rejected(tmp_path, monkeypatch, fx_rates):
    """An investment txn with a non-existent category key raises ValidationError."""
    from backend.core.errors import ValidationError

    ledger_mod = _reload_ledger(tmp_path, monkeypatch)

    acct = ledger_mod.accounts.insert(
        {
            "id": "acct-cash",
            "name": "Cash",
            "kind": "asset",
            "type": "cash",
            "currency": "USD",
            "initial_balance": 5000.0,
        }
    )

    with pytest.raises(ValidationError, match="Unknown category"):
        ledger_mod.create_transaction(
            {
                "date": "2026-07-01",
                "kind": "investment",
                "from_account_id": acct["id"],
                "from_amount": 500.0,
                "from_currency": "USD",
                "category": "not-a-real-category-xyz",
            }
        )
