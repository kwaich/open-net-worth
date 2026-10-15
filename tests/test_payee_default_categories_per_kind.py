"""Per-kind default categories on a payee (owner decision 2026-07-10).

A payee no longer carries ONE ``default_category`` that bleeds onto every kind — it
carries ``default_categories = {expense, income, transfer, investment}`` and the create
choke applies the default for the transaction's OWN kind. This kills the reported bug
(income/transfer/investment picking up a payee's *expense* category), while still letting
a refund payee auto-apply a contra-expense category on income when the user sets an income
default for it.

Precedence at the choke is unchanged otherwise: explicit > rule > payee (per-kind) > None.

Offline + isolated: transactions/accounts/rules live in tmp_path; categories + payees use the
conftest throwaway DATA_DIR (built-ins seeded, both cleared per test). base = SGD.
"""

from __future__ import annotations

import pytest

from backend.services import categories as cats
from backend.services import ledger as svc
from backend.services import payee_registry as pr


@pytest.fixture
def iso(tmp_path, monkeypatch, db_isolated):
    """Accounts/transactions are DB-backed now (plan 3 task 4) — db_isolated redirects
    them (and every other unpinned store) to a fresh, empty database."""
    monkeypatch.setattr(svc, "base_currency", lambda: "SGD")
    a = svc.accounts.create(
        {"name": "A", "kind": "asset", "type": "cash", "currency": "SGD", "initial_balance": 0}
    )
    b = svc.accounts.create(
        {"name": "B", "kind": "asset", "type": "cash", "currency": "SGD", "initial_balance": 0}
    )
    exp = cats.create_category("Snacks", "expense")
    inc = cats.create_category("Paycheck", "income")
    tr = cats.create_category("Internal Move", "transfer")
    inv = cats.create_category("Brokerage Flow", "investment")
    return {
        "a": a["id"],
        "b": b["id"],
        "exp": exp["id"],
        "inc": inc["id"],
        "tr": tr["id"],
        "inv": inv["id"],
    }


def _mk_payee(name: str, per_kind: dict) -> str:
    pid = pr.resolve_payee_id(name)
    pr.set_default_categories(pid, per_kind)
    return pid


# ── registry: per-kind resolution + cross-kind isolation ──────────────────────


def test_default_category_for_is_kind_scoped(iso):
    pid = _mk_payee("Example Ride", {"expense": iso["exp"], "income": iso["inc"]})
    assert pr.default_category_for(pid, "expense") == iso["exp"]
    assert pr.default_category_for(pid, "income") == iso["inc"]
    # a kind with no per-kind default returns None — NOT another kind's default.
    assert pr.default_category_for(pid, "transfer") is None
    assert pr.default_category_for(pid, "investment") is None


# ── ledger create choke: applies the txn's OWN kind default ───────────────────


def test_income_does_not_inherit_expense_default(iso):
    pid = _mk_payee("Example Ride", {"expense": iso["exp"]})  # NO income default
    t = svc.create_transaction(
        {
            "kind": "income",
            "to_account_id": iso["a"],
            "to_amount": 20,
            "date": "2026-07-01",
            "payee_id": pid,
        }
    )
    assert t["category"] is None  # was iso["exp"] before the fix


def test_income_uses_its_own_default_when_set(iso):
    pid = _mk_payee("Refund from GRAB", {"income": iso["exp"]})  # refund → contra-expense
    t = svc.create_transaction(
        {
            "kind": "income",
            "to_account_id": iso["a"],
            "to_amount": 12,
            "date": "2026-07-01",
            "payee_id": pid,
        }
    )
    assert t["category"] == iso["exp"]


def test_transfer_and_investment_use_their_own_defaults(iso):
    pid = _mk_payee("Multi", {"transfer": iso["tr"], "investment": iso["inv"]})
    tr = svc.create_transaction(
        {
            "kind": "transfer",
            "from_account_id": iso["a"],
            "from_amount": 10,
            "to_account_id": iso["b"],
            "to_amount": 10,
            "date": "2026-07-01",
            "payee_id": pid,
        }
    )
    assert tr["category"] == iso["tr"]
    inv = svc.create_transaction(
        {
            "kind": "investment",
            "from_account_id": iso["a"],
            "from_amount": 100,
            "date": "2026-07-02",
            "payee_id": pid,
        }
    )
    assert inv["category"] == iso["inv"]


def test_explicit_category_beats_payee_default(iso):
    pid = _mk_payee("Example Ride", {"expense": iso["exp"]})
    t = svc.create_transaction(
        {
            "kind": "expense",
            "from_account_id": iso["a"],
            "from_amount": 5,
            "date": "2026-07-01",
            "payee_id": pid,
            "category": iso["inc"],  # user picked something explicit
        }
    )
    assert t["category"] == iso["inc"]


def test_bulk_create_applies_per_kind_default(iso):
    pid = _mk_payee("Multi", {"transfer": iso["tr"], "investment": iso["inv"]})
    created, _ = svc.bulk_create_transactions(
        [
            {
                "kind": "transfer",
                "from_account_id": iso["a"],
                "from_amount": 10,
                "to_account_id": iso["b"],
                "to_amount": 10,
                "date": "2026-07-01",
                "payee_id": pid,
            },
            {
                "kind": "investment",
                "from_account_id": iso["a"],
                "from_amount": 50,
                "date": "2026-07-02",
                "payee_id": pid,
            },
        ]
    )
    assert created[0]["category"] == iso["tr"]
    assert created[1]["category"] == iso["inv"]


# ── learning is per-kind (does not overwrite across directions) ───────────────


def test_learn_category_is_per_kind(iso):
    pid = _mk_payee("Learner", {})  # no defaults yet
    svc.create_transaction(
        {
            "kind": "expense",
            "from_account_id": iso["a"],
            "from_amount": 5,
            "date": "2026-07-01",
            "payee_id": pid,
            "category": iso["exp"],
        }
    )
    svc.create_transaction(
        {
            "kind": "income",
            "to_account_id": iso["a"],
            "to_amount": 5,
            "date": "2026-07-02",
            "payee_id": pid,
            "category": iso["inc"],
        }
    )
    assert pr.default_category_for(pid, "expense") == iso["exp"]
    assert pr.default_category_for(pid, "income") == iso["inc"]  # not clobbered by expense


# ── set_default_categories validation ─────────────────────────────────────────


def test_set_default_categories_validates_and_drops(iso):
    pid = pr.resolve_payee_id("V")
    saved = pr.set_default_categories(
        pid,
        {
            "expense": iso["exp"],
            "income": "Paycheck",  # by label → resolves
            "adjustment": iso["exp"],  # not an allowed kind → dropped
            "transfer": "cat_missing000",  # unknown id → dropped
        },
    )
    assert saved["default_categories"] == {"expense": iso["exp"], "income": iso["inc"]}


# ── data-driven migration ─────────────────────────────────────────────────────
