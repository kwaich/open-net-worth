"""A figure built from spending says so while a credit card's spending is not in it yet.

Persona QA round 5 (2026-10-14, synthetic reproduction): a new user with a salary, a bank
account and a credit card whose purchases were never imported saw "Savings rate 85%" and "12.9
months runway". The numbers were true to the rows the book had and wrong about the person, with
nothing on screen to say so. Every spending-based figure now names the cards it cannot see.
"""

from __future__ import annotations

from datetime import timedelta

from backend.core.user_time import user_today
from backend.services import accounts as accounts_service
from backend.services import card_coverage, cashflow_projections, reports_rollup
from backend.services import cashflow as cashflow_service
from backend.services import ledger as ledger_service


def _book() -> None:
    accounts_service.create_account(
        {"name": "Current", "type": "cash", "currency": "SGD", "initial_balance": 20_000.0}
    )
    accounts_service.create_account(
        {"name": "Visa", "type": "credit", "currency": "SGD", "initial_balance": 0.0}
    )
    last_month = user_today().replace(day=1) - timedelta(days=5)
    for label, kind, amount in (("Salary", "income", 6_000.0), ("Rent", "expense", 1_500.0)):
        leg = "to" if kind == "income" else "from"
        ledger_service.create_transaction(
            {
                "date": last_month.isoformat(),
                "kind": kind,
                "description": label,
                f"{leg}_account_id": "Current",
                f"{leg}_amount": amount,
                f"{leg}_currency": "SGD",
            }
        )


def _card_purchase() -> None:
    ledger_service.create_transaction(
        {
            "date": (user_today().replace(day=1) - timedelta(days=3)).isoformat(),
            "kind": "expense",
            "description": "Groceries",
            "from_account_id": "Visa",
            "from_amount": 120.0,
            "from_currency": "SGD",
        }
    )


def test_an_empty_card_is_named_by_the_runway_and_the_savings_rate(db_isolated, fx_rates):
    _book()

    assert cashflow_service.cash_runway()["cards_missing_spending"] == ["Visa"]
    assert cashflow_projections.runway()["cards_missing_spending"] == ["Visa"]
    assert reports_rollup.reports_aggregates()["savings"]["cards_missing_spending"] == ["Visa"]


def test_a_card_with_a_purchase_is_not_flagged(db_isolated, fx_rates):
    _book()
    _card_purchase()

    assert cashflow_service.cash_runway()["cards_missing_spending"] == []
    assert reports_rollup.reports_aggregates()["savings"]["cards_missing_spending"] == []


def test_a_book_without_cards_has_nothing_to_caveat(db_isolated, fx_rates):
    accounts_service.create_account(
        {"name": "Current", "type": "cash", "currency": "SGD", "initial_balance": 1_000.0}
    )

    assert card_coverage.cards_without_activity("2000-01-01", "2999-12-31") == []
