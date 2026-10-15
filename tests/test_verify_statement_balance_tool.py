"""Statement balance verification: ignore adjustment rows, and distinguish a
matching statement net from a cash/card account whose opening hides imported rows.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from backend.ai.tools.statements import verify_statement_balance
from backend.services import accounts as accounts_service
from backend.services import ledger as ledger_service


def _call(**kwargs) -> dict:
    payload, _ = asyncio.run(verify_statement_balance(kwargs))
    return json.loads(payload)


@pytest.fixture
def env(db_isolated):
    ledger_service.accounts.create(
        {
            "id": "acc_card",
            "name": "Rewards Card",
            "kind": "liability",
            "type": "credit",
            "currency": "SGD",
        }
    )
    ledger_service.create_transaction(
        {
            "date": "2026-06-10",
            "kind": "expense",
            "from_account_id": "acc_card",
            "from_amount": 100.0,
            "from_currency": "SGD",
            "description": "Groceries",
        }
    )


def test_adjustment_row_in_window_does_not_move_actual_net_or_delta(env):
    """An ordinary expense alone: opening 0, closing -100 (card owes 100 more)."""
    baseline = _call(
        account_id="acc_card",
        opening_balance=0.0,
        closing_balance=-100.0,
        date_from="2026-06-01",
        date_to="2026-06-30",
    )
    assert baseline["ok"] is True
    assert baseline["status"] == "ok"

    # A manual reconcile-style adjustment inside the SAME window — diff_statement's
    # _ledger_net skips these; verify_statement_balance must too.
    ledger_service.create_transaction(
        {
            "date": "2026-06-15",
            "kind": "adjustment",
            "from_account_id": "acc_card",
            "from_amount": 25.0,
            "from_currency": "SGD",
            "description": "Balance adjustment — Rewards Card",
            "cleared": True,
        }
    )

    with_adjustment = _call(
        account_id="acc_card",
        opening_balance=0.0,
        closing_balance=-100.0,
        date_from="2026-06-01",
        date_to="2026-06-30",
    )
    assert with_adjustment["actual_net"] == baseline["actual_net"]
    assert with_adjustment["delta"] == baseline["delta"]
    assert with_adjustment["total_in"] == baseline["total_in"]
    assert with_adjustment["total_out"] == baseline["total_out"]
    assert with_adjustment["status"] == "ok"
    # The adjustment row itself must not be counted into transaction_count either —
    # it never appears on the statement diff_statement compares against.
    assert with_adjustment["transaction_count"] == baseline["transaction_count"]


def test_ordinary_expense_in_window_still_moves_actual_net_and_delta(env):
    """Sanity check: the fix must not blanket-exclude every transaction — a second real
    expense inside the window still changes the computed net (and, left unmatched
    against the statement's closing balance, produces a mismatch)."""
    before = _call(
        account_id="acc_card",
        opening_balance=0.0,
        closing_balance=-100.0,
        date_from="2026-06-01",
        date_to="2026-06-30",
    )
    assert before["status"] == "ok"

    ledger_service.create_transaction(
        {
            "date": "2026-06-20",
            "kind": "expense",
            "from_account_id": "acc_card",
            "from_amount": 40.0,
            "from_currency": "SGD",
            "description": "Dining",
        }
    )

    after = _call(
        account_id="acc_card",
        opening_balance=0.0,
        closing_balance=-100.0,
        date_from="2026-06-01",
        date_to="2026-06-30",
    )
    assert after["actual_net"] != before["actual_net"]
    assert after["status"] == "mismatch"
    assert after["total_out"] == pytest.approx(before["total_out"] + 40.0)


def test_matching_statement_net_flags_rows_hidden_before_account_opening(db_isolated):
    account = accounts_service.create_account(
        {
            "name": "QA Bank",
            "type": "cash",
            "currency": "SGD",
            "initial_balance": 1000.0,
            "initial_balance_date": "2026-09-28",
        }
    )
    ledger_service.create_transaction(
        {
            "date": "2026-09-07",
            "kind": "income",
            "to_account_id": account["id"],
            "to_amount": 13.08,
            "to_currency": "SGD",
            "description": "Statement credit",
        }
    )

    result = _call(
        account_id=account["id"],
        opening_balance=1000.0,
        closing_balance=1013.08,
        date_from="2026-09-01",
        date_to="2026-09-30",
    )

    assert result["delta"] == 0
    assert result["status"] == "tracking_start_conflict"
    assert result["tracking_start"] == "2026-09-28"
    assert result["excluded_transaction_count"] == 1
    assert "don't change its balance" in result["note"]


def test_net_mismatch_remains_explicit_when_rows_are_also_hidden(db_isolated):
    account = accounts_service.create_account(
        {
            "name": "QA Bank",
            "type": "cash",
            "currency": "SGD",
            "initial_balance": 1000.0,
            "initial_balance_date": "2026-09-28",
        }
    )
    ledger_service.create_transaction(
        {
            "date": "2026-09-07",
            "kind": "income",
            "to_account_id": account["id"],
            "to_amount": 13.08,
            "to_currency": "SGD",
            "description": "Statement credit",
        }
    )

    result = _call(
        account_id=account["id"],
        opening_balance=1000.0,
        closing_balance=1014.08,
        date_from="2026-09-01",
        date_to="2026-09-30",
    )

    assert result["status"] == "mismatch"
    assert result["tracking_start_conflict"] is True
    assert result["excluded_transaction_count"] == 1
    assert "don't change its balance" in result["note"]
    assert "doesn't match" in result["note"]


def test_investment_pre_opening_activity_is_not_reported_as_cash_conflict(db_isolated):
    account = accounts_service.create_account(
        {
            "name": "QA Brokerage",
            "type": "investment",
            "currency": "SGD",
            "initial_balance": 1000.0,
            "initial_balance_date": "2026-09-28",
        }
    )
    ledger_service.create_transaction(
        {
            "date": "2026-09-07",
            "kind": "income",
            "to_account_id": account["id"],
            "to_amount": 13.08,
            "to_currency": "SGD",
            "description": "Historical brokerage activity",
        }
    )

    result = _call(
        account_id=account["id"],
        opening_balance=1000.0,
        closing_balance=1013.08,
        date_from="2026-09-01",
        date_to="2026-09-30",
    )

    assert result["status"] == "ok"
    assert result["tracking_start_conflict"] is False
    assert "excluded_transaction_count" not in result


def test_a_card_statement_s_owed_amounts_are_read_as_printed(env):
    """A card statement prints what is OWED as positive numbers and spending raises it. Passed
    "as shown on the statement", 0 → 100 owed is the same 100 of spending — not a mismatch
    (QA 2026-10-13 reported "a gap of 2,181.92" on a card that tied to the cent)."""
    out = _call(
        account_id="Rewards Card",
        opening_balance=0.0,
        closing_balance=100.0,
        date_from="2026-06-01",
        date_to="2026-06-30",
    )
    assert out["status"] == "ok"
    assert out["balances_read_as"].startswith("amount owed")


def test_a_payment_left_out_on_purpose_is_not_a_mismatch(env, monkeypatch):
    """The statement had the purchase AND a 40 payment; the payment was deliberately left to
    the bank's side. The booked rows differ from the statement by exactly that row, so the
    check says the statement ties and names what was left out instead of "mismatch"."""
    from backend.db import statement_store

    batch = "imp_card_aug"
    rows = ledger_service.transactions.all()
    for row in rows:  # the fixture's purchase came in through this import batch
        if row.get("description") == "Groceries":
            row["batch_id"] = batch
    monkeypatch.setattr(ledger_service.transactions, "all", lambda: rows)
    record = {
        "id": "stmt_t",
        "batch_ids": [batch],
        "import_rows": [
            {"direction": "OUT", "amount": 100.0},
            {"direction": "IN", "amount": 40.0},
        ],
    }
    monkeypatch.setattr(statement_store.StatementStore, "all", lambda self: [record])
    out = _call(account_id="acc_card", opening_balance=40.0, closing_balance=100.0, batch_id=batch)
    assert out["status"] == "ok_rows_left_out"
    assert out["rows_left_out"] == {"net": 40.0, "rows": 1}
    assert "Nothing is missing" in out["left_out_note"]


def test_a_genuinely_missing_row_still_reports_a_mismatch(env, monkeypatch):
    """Left-out rows only explain the gap when the STATEMENT itself ties; otherwise the
    honest answer is still a mismatch."""
    out = _call(
        account_id="acc_card",
        opening_balance=0.0,
        closing_balance=250.0,  # owes 250, but only 100 of spending is booked
        date_from="2026-06-01",
        date_to="2026-06-30",
    )
    assert out["status"] == "mismatch"


def test_a_gap_is_reported_as_close_never_as_reconciling(env):
    """QA 2026-10-14: a reply opened "the statement reconciles" and then reported a S$47.24
    gap. A mismatch result tells the model what to say instead."""
    out = _call(
        account_id="acc_card",
        opening_balance=0.0,
        closing_balance=-147.24,
        date_from="2026-06-01",
        date_to="2026-06-30",
    )
    assert out["status"] == "mismatch"
    assert "Do NOT tell the user it reconciles" in out["note"]
    assert "close, with a gap of 47.24" in out["note"]
