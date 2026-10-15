"""Net worth on a past date agrees with the history's rebuild (synthetic data only).

Live 2026-10-07: the weekly report could only print net worth "Not shown" — the history
charts month-ends, so a Sunday had no point.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from backend.core.errors import ValidationError
from backend.services import ledger as svc


@pytest.fixture
def book(db_isolated, monkeypatch):
    monkeypatch.setattr(svc, "base_currency", lambda: "SGD")
    acct = svc.accounts.create(
        {
            "name": "Meridian Checking",
            "kind": "asset",
            "type": "cash",
            "currency": "SGD",
            "initial_balance": 0,
        }
    )
    return acct["id"]


def _income(acct, when, amount):
    svc.create_transaction(
        {
            "kind": "income",
            "date": when.isoformat(),
            "description": "Pay",
            "to_account_id": acct,
            "to_amount": amount,
            "to_currency": "SGD",
        }
    )


def test_a_mid_month_date_has_its_own_net_worth(book):
    today = date.today()
    _income(book, today - timedelta(days=20), 1000.0)
    _income(book, today - timedelta(days=5), 250.0)

    before = svc.net_worth_on((today - timedelta(days=10)).isoformat())
    after = svc.net_worth_on((today - timedelta(days=1)).isoformat())

    assert before["net_worth"] == 1000.0
    assert after["net_worth"] == 1250.0


def test_a_month_end_matches_the_history_point(book):
    today = date.today()
    first = today.replace(day=1)
    month_end = first - timedelta(days=1)
    _income(book, month_end - timedelta(days=3), 400.0)

    history = {p["date"]: p["net_worth"] for p in svc.net_worth_history()}

    assert svc.net_worth_on(month_end.isoformat())["net_worth"] == history[month_end.isoformat()]


def test_today_or_a_future_date_is_refused(book):
    with pytest.raises(ValidationError):
        svc.net_worth_on(date.today().isoformat())
    with pytest.raises(ValidationError):
        svc.net_worth_on("not-a-date")
