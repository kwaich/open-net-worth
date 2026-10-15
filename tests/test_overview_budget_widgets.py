"""Overview budget widgets (`planning_summary.budget_aggregates`): a credit-card PAYMENT
envelope is not spending, and Goals reads the user's savings goals.

Mirrors the `env` isolation of tests/test_planning_summary.py (fresh DB, SGD, one bank), plus
an Amex in `payment_envelope` mode."""

from __future__ import annotations

import datetime

import pytest

from backend.core.ids import new_id
from backend.db.category_store import CategoryStore
from backend.db.profile_store import ProfileStore
from backend.db.targets_store import TargetsStore
from backend.models.category_defs import BUILTIN_CATEGORIES
from backend.services import budget_lens, planning, planning_summary
from backend.services import envelopes as envelopes_service
from backend.services import ledger as svc
from backend.services import targets as targets_service

_FOOD = "cat_13095ba70000"  # Food & Dining
_MONTH = "2026-06"


@pytest.fixture
def env(monkeypatch, db_isolated):
    monkeypatch.setattr(planning, "load_fx_cache", lambda: {"SGD": 1.0})
    monkeypatch.setattr(svc, "base_currency", lambda: "SGD")
    monkeypatch.setattr(budget_lens, "load_fx_cache", lambda: {"SGD": 1.0})
    monkeypatch.setattr(targets_service.svc, "base_currency", lambda: "SGD")
    monkeypatch.setattr(targets_service, "load_fx_cache", lambda: {"SGD": 1.0})
    monkeypatch.setattr(targets_service, "_today", lambda: datetime.date(2026, 6, 15))
    ProfileStore().set_raw(
        {
            "base_currency": "SGD",
            "credit_card_mode": "payment_envelope",
            "budget_config": {"rollover_start": _MONTH},
        }
    )
    CategoryStore().bulk_insert(list(BUILTIN_CATEGORIES))
    svc.transactions.replace_all([])
    svc.accounts.replace_all(
        [
            {
                "id": "bank",
                "name": "Bank",
                "kind": "asset",
                "type": "cash",
                "currency": "SGD",
                "opening_txn_id": None,
            },
            {
                "id": "amex",
                "name": "Amex",
                "kind": "liability",
                "type": "credit",
                "currency": "SGD",
                "opening_txn_id": None,
            },
        ]
    )
    envelopes_service.ensure_envelopes_migrated()
    planning.sync_card_payment_envelopes()


def _txn(kind, d, amount, **legs):
    return {"id": new_id("txn"), "kind": kind, "date": d, "base_amount": amount, **legs}


def _seed_card_month():
    """Food assigned 500; 300 of it spent ON THE CARD, then 300 paid from the bank."""
    rows = [
        _txn("income", "2026-06-01", 2000, to_account_id="bank", to_amount=2000, to_currency="SGD"),
        _txn(
            "expense",
            "2026-06-05",
            300,
            category=_FOOD,
            from_account_id="amex",
            from_amount=300,
            from_currency="SGD",
        ),
        _txn(
            "transfer",
            "2026-06-10",
            300,
            from_account_id="bank",
            from_amount=300,
            from_currency="SGD",
            to_account_id="amex",
            to_amount=300,
            to_currency="SGD",
        ),
    ]
    svc.transactions.replace_all(rows)
    planning.apply_budget_change(
        {
            "type": "assign",
            "month": _MONTH,
            "envelope_id": envelopes_service.env_of()[_FOOD],
            "amount": 500,
        }
    )
    pay_env = svc.accounts.find_by_id("amex")["payment_envelope_id"]
    planning.apply_budget_change(
        {"type": "assign", "month": _MONTH, "envelope_id": pay_env, "amount": 300}
    )
    return pay_env


def test_card_payment_envelope_is_not_spending_or_budget(env):
    pay_env = _seed_card_month()
    table = planning.budget_month(_MONTH)
    assert pay_env in planning_summary._payment_envelope_keys(table)

    envelopes = planning_summary.budget_aggregates(_MONTH)["envelopes"]

    assert pay_env not in {r["key"] for r in envelopes["rows"]}
    assert pay_env not in {r["key"] for r in envelopes["budgeted_rows"]}
    assert envelopes["total_spent"] == 300.0
    assert envelopes["budgeted_spent"] == 300.0
    assert envelopes["total_budget"] == 500.0
    assert envelopes["budgeted_total"] == 500.0
    assert envelopes["pct_of_budget"] == 60.0


def test_book_without_a_payment_envelope_keeps_its_rows_and_totals(env):
    envelopes = {"rows": [], "total_budget": 0.0, "budgeted_spent": 0.0, "budgeted_total": 0.0}
    out = planning_summary._without_payment_envelopes(envelopes, set())
    assert {k: v for k, v in out.items() if k != "pct_of_budget"} == envelopes
    assert out["pct_of_budget"] == 0.0


def test_pct_of_budget_compares_budgeted_spend_with_budgets(env):
    """Unbudgeted spending isn't "over budget": 900 spent mostly outside three savings
    envelopes that got 1,000 and spent 100 reads 10%, not 90% or 369%."""
    envelopes = {
        "rows": [],
        "total_budget": 1_000.0,
        "total_spent": 900.0,
        "budgeted_spent": 100.0,
        "budgeted_total": 1_000.0,
    }
    assert planning_summary._without_payment_envelopes(envelopes, set())["pct_of_budget"] == 10.0


def test_without_payment_envelopes_recomputes_every_total():
    def row(key, assigned, spent):
        return {"key": key, "assigned": assigned, "spent": spent}

    envelopes = {
        "rows": [row("pay", 250, 250), row("food", 400, 100), row("misc", 0, 50)],
        "budgeted_rows": [row("pay", 250, 250), row("food", 400, 100)],
        "total_spent": 400.0,
        "budgeted_spent": 350.0,
        "budgeted_total": 650.0,
        "total_budget": 650.0,
        "pct_of_budget": 61.54,
    }
    out = planning_summary._without_payment_envelopes(envelopes, {"pay"})
    assert [r["key"] for r in out["rows"]] == ["food", "misc"]
    assert [r["key"] for r in out["budgeted_rows"]] == ["food"]
    assert out["total_spent"] == 150.0
    assert out["budgeted_spent"] == 100.0
    assert out["budgeted_total"] == 400.0
    assert out["total_budget"] == 400.0
    assert out["pct_of_budget"] == 25.0


def test_goals_widget_lists_the_users_savings_goals(env):
    store = TargetsStore()
    store.create(
        {
            "name": "Emergency fund",
            "target_amount": 1000,
            "current_amount": 250,
            "currency": "SGD",
            "target_date": "2027-06-15",
        }
    )
    store.create(
        {
            "name": "Holiday",
            "target_amount": 2000,
            "current_amount": 1500,
            "currency": "SGD",
            "target_date": "2026-12-15",
        }
    )
    cancelled = store.create({"name": "Dropped", "target_amount": 10, "target_date": "2027-01-01"})
    store.update_target(cancelled["id"], {"status": "cancelled"})

    goals = planning_summary.budget_aggregates(_MONTH)["goals"]

    assert [g["label"] for g in goals] == ["Emergency fund", "Holiday"]
    emergency, holiday = goals
    assert (emergency["have"], emergency["target"], emergency["pct"]) == (250.0, 1000.0, 25.0)
    assert emergency["by"] == "2027-06-15"
    assert holiday["pct"] == 75.0
    assert holiday["risk"] is False
    assert emergency["status"] == "on_track"


def test_past_due_goal_is_flagged_at_risk(env):
    TargetsStore().create(
        {"name": "Late", "target_amount": 500, "current_amount": 100, "target_date": "2026-05-01"}
    )
    (goal,) = planning_summary.budget_aggregates(_MONTH)["goals"]
    assert goal["status"] == "past_due"
    assert goal["risk"] is True
