"""Reports count history the budget leaves out: rows dated before an account's Starting
Balance, and money in that has no category yet.

Persona QA 2026-10-13: a new user opened DBS at today's balance, imported last month's
statement, asked where her money went, and Dashboard › Reports said "No income or spending
recorded in Sep." The budget is right to skip those rows (the opening balance already holds
them); a report of where money went is not.
"""

from __future__ import annotations

import pytest

from backend.services import accounts as accounts_service
from backend.services import categories as categories_service
from backend.services import ledger as ledger_service
from backend.services import planning, reports_rollup


@pytest.fixture
def book(db_isolated, monkeypatch):
    monkeypatch.setattr(ledger_service, "base_currency", lambda: "SGD")
    monkeypatch.setattr(reports_rollup, "base_currency", lambda: "SGD")
    acct = accounts_service.create_account(
        {
            "name": "QA Bank",
            "type": "cash",
            "currency": "SGD",
            "initial_balance": 1000.0,
            "initial_balance_date": "2026-09-28",
        }
    )
    groceries = categories_service.create_category("QA Groceries")["id"]

    def spend(day, amount, category=None, payee="QA Mart"):
        ledger_service.create_transaction(
            {
                "date": day,
                "kind": "expense",
                "from_account_id": acct["id"],
                "from_amount": amount,
                "from_currency": "SGD",
                "description": payee,
                **({"category": category} if category else {}),
            }
        )

    ledger_service.create_transaction(
        {
            "date": "2026-09-01",
            "kind": "income",
            "to_account_id": acct["id"],
            "to_amount": 5400.0,
            "to_currency": "SGD",
            "description": "QA salary",
        }
    )
    spend("2026-09-07", 72.35, groceries)  # history: before the Starting Balance
    spend("2026-09-10", 40.0, payee="QA Kiosk")  # history, uncategorized (its own payee)
    spend("2026-09-29", 10.0, groceries)  # after the Starting Balance: the budget counts it
    return groceries


def test_history_shows_in_reports(book):
    out = reports_rollup.month_summary("2026-09")
    by_label = {r["label"]: r["amount"] for r in out["expense_rows"]}
    assert by_label["QA Groceries"] == pytest.approx(82.35)  # history 72.35 + tracked 10, once each
    assert by_label["Uncategorized"] == pytest.approx(40.0)
    assert out["expense_total"] == pytest.approx(122.35)
    assert out["income_total"] == pytest.approx(5400.0)  # the unsorted salary is income


def test_the_budget_still_leaves_history_out(book):
    month = planning.budget_month("2026-09")
    activity = {
        c.get("label"): c.get("activity")
        for g in month.get("groups") or []
        for c in g.get("categories") or []
    }
    assert activity.get("QA Groceries", 0) == pytest.approx(-10.0)  # tracked row only


def test_history_is_only_what_the_cutoff_removes(book):
    assert planning.history_activity_by_category("2026-09") == {
        book: pytest.approx(-72.35),
        "": pytest.approx(-40.0),
    }
