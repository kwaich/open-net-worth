"""The Next-30-days feed lists recurring bills and card statements beside commitments.

Owner report (2026-10-15, reproduced with synthetic data): the card showed the salary coming in
but not the rent going out — only commitments were listed as money out, so "going out" read as
the month being cheaper than it is. Recurring outflows and card bills due in the window are now
listed and counted in `going_out_total_base`; the commitments-only `strip_total_base` (the
figure the Commitments masthead quotes) is unchanged.
"""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

from backend.core.user_time import user_today
from backend.db.commitments_store import CommitmentsStore
from backend.db.recurring_stream_store import RecurringStreamStore
from backend.services import commitments_summary, upcoming_bills


def _day(offset: int) -> str:
    return (user_today() + timedelta(days=offset)).isoformat()


def _declare(direction: str, label: str, amount: float, anchor: str) -> None:
    RecurringStreamStore().create_declared(
        {
            "direction": direction,
            "label": label,
            "amount": amount,
            "currency": "SGD",
            "cadence": "monthly",
            "anchor_date": anchor,
        }
    )


def _commitment(offset: int, amount: float) -> None:
    CommitmentsStore().create(
        {
            "id": "loan",
            "name": "Car loan",
            "kind": "loan_repayment",
            "currency": "SGD",
            "installments": [
                {
                    "id": "i1",
                    "label": "Car loan auto-debit",
                    "due_date": _day(offset),
                    "amount": amount,
                }
            ],
        }
    )


def _no_cards(monkeypatch) -> None:
    monkeypatch.setattr(upcoming_bills, "card_bills", lambda *a, **k: [])


def test_a_recurring_bill_is_listed_and_counted_as_going_out(db_isolated, fx_rates, monkeypatch):
    _no_cards(monkeypatch)
    _declare("out", "Rent", 2_400.0, _day(6))
    _commitment(12, 900.0)

    overview = commitments_summary.commitments_overview()

    assert [(e["date"], e["label"], e["amount_base"]) for e in overview["bills"]] == [
        (_day(6), "Rent", 2_400.0)
    ]
    assert overview["strip_total_base"] == 900.0
    assert overview["going_out_total_base"] == 3_300.0


def test_a_bill_a_commitment_already_tracks_is_not_counted_twice(
    db_isolated, fx_rates, monkeypatch
):
    _no_cards(monkeypatch)
    _commitment(10, 1_250.0)
    _declare("out", "Car loan", 1_250.0, _day(11))

    overview = commitments_summary.commitments_overview()

    assert overview["bills"] == []
    assert overview["going_out_total_base"] == 1_250.0


def test_a_card_statement_due_in_the_window_is_a_bill(db_isolated, fx_rates, monkeypatch):
    class _Accounts:
        def query_accounts(self, type=None):
            return [
                {"id": "due", "name": "Visa", "type": "credit"},
                {"id": "paid", "name": "Amex", "type": "credit"},
                {"id": "late", "name": "Master", "type": "credit"},
                {"id": "far", "name": "Diners", "type": "credit"},
            ]

    def _summary(account_id, today=None, transactions_store=None):
        owed, days = {"due": (640.0, 9), "paid": (0.0, 4), "late": (300.0, -2), "far": (80.0, 40)}[
            account_id
        ]
        return SimpleNamespace(
            next_due_date=_day(days),
            days_until_due=days,
            remaining_amount=owed,
            currency="SGD",
            due_source="statement",
        )

    monkeypatch.setattr(upcoming_bills, "AccountStore", _Accounts)
    monkeypatch.setattr(upcoming_bills, "statement_summary", _summary)

    overview = commitments_summary.commitments_overview()

    assert [(e["label"], e["date"], e["amount_base"]) for e in overview["card_bills"]] == [
        ("Visa", _day(9), 640.0)
    ]
    assert overview["going_out_total_base"] == 640.0


def test_needs_attention_flags_only_a_card_bill_due_within_a_week_or_overdue(
    db_isolated, fx_rates, monkeypatch
):
    """Owner decision (2026-10-16): owing money on a card is not a problem, a bill about to fall
    due is. The Dashboard's card flag reads `cards_due_soon`: unpaid, and due within 7 days or
    past its due date — never a paid statement or one weeks away."""

    class _Accounts:
        def query_accounts(self, type=None):
            return [
                {"id": "soon", "name": "Visa", "type": "credit"},
                {"id": "late", "name": "Master", "type": "credit"},
                {"id": "week", "name": "Amex", "type": "credit"},
                {"id": "paid", "name": "Diners", "type": "credit"},
                {"id": "later", "name": "Discover", "type": "credit"},
            ]

    def _summary(account_id, today=None, transactions_store=None):
        owed, days = {
            "soon": (640.0, 3),
            "late": (300.0, -2),
            "week": (120.0, 7),
            "paid": (0.0, 1),
            "later": (80.0, 8),
        }[account_id]
        return SimpleNamespace(
            next_due_date=_day(days),
            days_until_due=days,
            remaining_amount=owed,
            currency="SGD",
            due_source="statement" if account_id != "week" else "config",
        )

    monkeypatch.setattr(upcoming_bills, "AccountStore", _Accounts)
    monkeypatch.setattr(upcoming_bills, "statement_summary", _summary)

    overview = commitments_summary.commitments_overview()

    assert [
        (e["label"], e["days_until_due"], e["amount_base"], e["estimated"])
        for e in overview["cards_due_soon"]
    ] == [("Master", -2, 300.0, False), ("Visa", 3, 640.0, False), ("Amex", 7, 120.0, True)]
    # The Next 30 days list is unchanged: an overdue statement is not "coming up".
    assert [e["label"] for e in overview["card_bills"]] == ["Visa", "Amex", "Discover"]
