"""One definition of "overdue" and "due in the next 30 days", read by every screen.

Persona QA round 5 (2026-10-14, a copy of a real book; reproduced here with synthetic data):
the briefing and Commitments said 8 overdue while the Dashboard's Next-30-days card said 6, and
Commitments showed "Due in 30 days S$0.00" while the card listed a loan auto-debit due tomorrow
and a set-aside on the 31st. Each surface had its own filter (funding inflows counted as overdue
here, trackers skipped there, a window of 14 days in the briefing). `commitments.due_summary` is
now the only place the rule lives; the summary endpoint and the briefing both read it.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

import backend.api.briefing as brf
from backend.core.user_time import user_today
from backend.db.commitments_store import CommitmentsStore
from backend.services import commitments as commitments_service
from backend.services import commitments_summary


@pytest.fixture
def book(db_isolated, monkeypatch, fx_rates):
    monkeypatch.setattr(brf, "read_profile", lambda: {"base_currency": "SGD"})
    return db_isolated


def _day(offset: int) -> str:
    return (user_today() + timedelta(days=offset)).isoformat()


def _installment(i: str, label: str, offset: int, amount: float) -> dict:
    return {"id": i, "label": label, "due_date": _day(offset), "amount": amount}


def _seed_book() -> None:
    """Overdue installments and actions, an auto-debit due tomorrow, a set-aside on day 25,
    one thing beyond the window, a closed action and a late funding inflow."""
    store = CommitmentsStore()
    store.create(
        {
            "id": "reno",
            "name": "Renovation Loan",
            "kind": "loan_repayment",
            "currency": "SGD",
            "installments": [
                _installment("o1", "overdue 1", -9, 1_000.0),
                _installment("o2", "overdue 2", -4, 2_000.0),
                _installment("paid", "already paid", -20, 9_999.0) | {"status": "paid"},
                _installment("far", "beyond the window", 45, 7_777.0),
            ],
            "actions": [
                {"id": "a1", "label": "Sign the form", "due_date": _day(-2), "status": "pending"},
                {
                    "id": "a2",
                    "label": "Called off",
                    "due_date": _day(-3),
                    "status": "missed",
                    "amount": 555.0,
                },
            ],
            "funding_plan": [
                {
                    "id": "f1",
                    "label": "Share sale",
                    "type": "asset_sale",
                    "amount": 50_000.0,
                    "currency": "SGD",
                    "available_date": _day(-6),
                }
            ],
        }
    )
    store.create(
        {
            "id": "car",
            "name": "Car Loan",
            "kind": "auto_pay",
            "currency": "SGD",
            "installments": [_installment("debit", "monthly debit", 1, 1_234.0)],
        }
    )
    store.create(
        {
            "id": "rainy",
            "name": "Rainy Day Fund",
            "kind": "goal",
            "currency": "SGD",
            "installments": [_installment("setaside", "set aside", 25, 3_000.0)],
        }
    )


def test_the_one_definition(book):
    _seed_book()
    summary = commitments_service.due_summary()

    assert {e["label"] for e in summary["overdue"]} == {"overdue 1", "overdue 2", "Sign the form"}
    assert summary["overdue_count"] == 3
    assert summary["overdue_total_base"] == 3_000.0
    assert {e["label"] for e in summary["due"]} == {"monthly debit", "set aside"}
    assert summary["due_total_base"] == 4_234.0
    assert [e["label"] for e in summary["funding_overdue"]] == ["Share sale"]


def test_briefing_and_the_next_30_days_summary_agree(book):
    _seed_book()
    overview = commitments_summary.commitments_overview()
    ctx = brf._build_context(0.0)

    assert overview["overdue_count"] == 3
    assert overview["overdue_total_base"] == 3_000.0
    assert overview["strip_total_base"] == 4_234.0
    assert "OVERDUE (3 unpaid or not done, S$3,000 in all" in ctx
    assert "DUE IN THE NEXT 30 DAYS (2, S$4,234 going out)" in ctx
    assert "monthly debit" in ctx and "set aside" in ctx
    # A late funding inflow is reported, but never as an overdue obligation.
    assert "FUNDING PAST ITS DATE (1" in ctx
    assert "beyond the window" not in ctx
    assert "Called off" not in ctx


def test_the_summary_endpoint_carries_the_overdue_list_the_lane_reads(book):
    _seed_book()
    overview = commitments_summary.commitments_overview()

    assert {e["label"] for e in overview["overdue"]} == {"overdue 1", "overdue 2", "Sign the form"}
    assert all(e["kind"] != "funding" for e in overview["overdue"])
    assert overview["overdue_count"] == len(overview["overdue"])
