"""Behavioral tests for the ``get_card_statement_status`` AI tool (RFC
cross-channel-attachments §8.1).

The tool is a bounded, deterministic read backed directly by
``services.card_statements.statement_summary`` — it exists precisely so Lucius never has to
guess a card's payment state from its current balance or from a transaction that merely looks
like a payment. This file exercises a SYNTHETIC dataset covering a real-world shape (one card
paid, one card paid, one card unpaid, one card partial/overdue with 654 remaining) plus the two
statuses that shape doesn't exercise (missing-cycle, unreconciled), so all 5 deterministic
statuses are covered end to end.

Real DB (``db_isolated``), real accounts/transactions/statement records — this exercises the
tool exactly as it runs in production, with ONE frozen "today" so the day-relative cycle/due
math is deterministic.
"""

from __future__ import annotations

import asyncio
import json
from datetime import date

import pytest

from backend.ai.tools.statements import get_card_statement_status
from backend.services import ledger as ledger_service

TODAY = date(2026, 8, 20)


class _FrozenDate(date):
    """Freezes card_statements' own notion of "today" without touching the tool, which
    never exposes a today= override to the model — that would let it fake the current date."""

    @classmethod
    def today(cls):
        return cls(TODAY.year, TODAY.month, TODAY.day)


@pytest.fixture(autouse=True)
def env(monkeypatch, db_isolated):
    import backend.services.card_statements as cs_mod

    monkeypatch.setattr(cs_mod, "user_today", _FrozenDate.today)
    monkeypatch.setattr(ledger_service, "base_currency", lambda: "SGD")

    ledger_service.accounts.create(
        {"id": "acc_bank", "name": "Bank", "kind": "asset", "type": "cash", "currency": "SGD"}
    )
    ledger_service.accounts.create(
        {
            "id": "acc_checking",
            "name": "Everyday Checking",
            "kind": "asset",
            "type": "cash",
            "currency": "SGD",
        }
    )
    for acc_id, name in (
        ("acc_card1", "Meridian Bank Rewards"),
        ("acc_card2", "Meridian Bank Multiplier"),
        ("acc_card3", "Meridian Bank Signature"),
        ("acc_card4", "Meridian Bank One"),
        ("acc_amex", "Amex"),
        ("acc_maybank", "Maybank"),
        ("acc_card5", "Lighthouse Bank"),
        ("acc_card6", "Overpaid Bank"),
    ):
        ledger_service.accounts.create(
            {
                "id": acc_id,
                "name": name,
                "kind": "liability",
                "type": "credit",
                "currency": "SGD",
                "statement_close_day": 5 if acc_id != "acc_maybank" else None,
            }
        )

    def _statement(sid: str, account_id: str, **meta):
        ledger_service.statements.insert(
            {
                "id": sid,
                "created_at": "2026-08-06T00:00:00Z",
                "filename": f"{sid}.pdf",
                "mime": "application/pdf",
                "size_bytes": 100,
                "file_hash": sid,
                "file_path": "",
                "kind": "pdf_pages",
                "upload_entry": {},
                "parsed_rows": None,
                "parsed_at": None,
                "account_id": account_id,
                "batch_ids": [],
                "import_meta": meta,
            }
        )

    def _payment(txn_id: str, to_id: str, amount: float, when: str):
        ledger_service.create_transaction(
            {
                "id": txn_id,
                "date": when,
                "kind": "transfer",
                "description": "Card Payment",
                "from_account_id": "acc_bank",
                "from_amount": amount,
                "from_currency": "SGD",
                "to_account_id": to_id,
                "to_amount": amount,
                "to_currency": "SGD",
                "source": "manual",
            }
        )

    def _charge(txn_id: str, from_id: str, amount: float, when: str):
        ledger_service.create_transaction(
            {
                "id": txn_id,
                "date": when,
                "kind": "expense",
                "description": "Charge",
                "from_account_id": from_id,
                "from_amount": amount,
                "from_currency": "SGD",
                "source": "manual",
            }
        )

    # Meridian Bank Rewards — paid in full.
    _statement(
        "stmt_card1",
        "acc_card1",
        statement_date="2026-08-05",
        payment_due_date="2026-08-25",
        amount_due=500.0,
        period_start="2026-07-06",
        period_end="2026-08-05",
    )
    _payment("t_card1_pay", "acc_card1", 500.0, "2026-08-10")

    # Meridian Bank Multiplier — paid in full.
    _statement(
        "stmt_card2",
        "acc_card2",
        statement_date="2026-08-05",
        payment_due_date="2026-08-25",
        amount_due=750.0,
        period_start="2026-07-06",
        period_end="2026-08-05",
    )
    _payment("t_card2_pay", "acc_card2", 750.0, "2026-08-11")

    # Meridian Bank Signature — unpaid, no payment at all.
    _statement(
        "stmt_card3",
        "acc_card3",
        statement_date="2026-08-05",
        payment_due_date="2026-08-25",
        amount_due=300.0,
        period_start="2026-07-06",
        period_end="2026-08-05",
    )

    # Meridian Bank One — partial + overdue: 1000 due 08-15 (past "today"), only 346 paid ->
    # 654 remaining.
    _statement(
        "stmt_card4",
        "acc_card4",
        statement_date="2026-08-05",
        payment_due_date="2026-08-15",
        amount_due=1000.0,
        period_start="2026-07-06",
        period_end="2026-08-05",
    )
    _payment("t_card4_pay", "acc_card4", 346.0, "2026-08-12")

    # Amex — the OLDER (July) statement is fully paid; a NEWER cycle has since closed with
    # no statement uploaded for it -> missing-cycle, driven by the ledger's own estimate.
    _statement(
        "stmt_amex",
        "acc_amex",
        statement_date="2026-07-05",
        payment_due_date="2026-07-25",
        amount_due=200.0,
        period_start="2026-06-06",
        period_end="2026-07-05",
    )
    _payment("t_amex_pay", "acc_amex", 200.0, "2026-07-10")
    _charge("t_amex_charge", "acc_amex", 400.0, "2026-07-20")

    # Lighthouse Bank — paid in full, but the due date has since elapsed relative to "today"
    # (2026-08-20): regression for a real repro, where a payment posted well before its due
    # date was reported by Lucius as "N days late" off the tool's stale overdue flag.
    _statement(
        "stmt_card5",
        "acc_card5",
        statement_date="2026-08-05",
        payment_due_date="2026-08-15",
        amount_due=400.0,
        period_start="2026-07-06",
        period_end="2026-08-05",
    )
    _payment("t_card5_pay", "acc_card5", 400.0, "2026-08-10")

    # Overpaid Bank — MATH-45: statement 200, payment 250. The raw difference is -50;
    # remaining_amount must clamp to 0, never go negative.
    _statement(
        "stmt_card6",
        "acc_card6",
        statement_date="2026-08-05",
        payment_due_date="2026-08-25",
        amount_due=200.0,
        period_start="2026-07-06",
        period_end="2026-08-05",
    )
    _payment("t_card6_pay", "acc_card6", 250.0, "2026-08-10")

    # Maybank — no cycle configured, no statement ever uploaded -> unreconciled.


def _call(**kwargs) -> dict:
    payload, _ = asyncio.run(get_card_statement_status(kwargs))
    return json.loads(payload)


def _row(result: dict, account_id: str) -> dict:
    matches = [r for r in result["statements"] if r["account_id"] == account_id]
    assert matches, f"{account_id} missing from {result['statements']}"
    return matches[0]


def test_card1_is_paid():
    row = _row(_call(), "acc_card1")
    assert row["status"] == "paid"
    assert row["remaining_amount"] == 0.0
    assert row["evidence_ids"] == ["stmt_card1", "t_card1_pay"]


def test_card2_is_paid():
    row = _row(_call(), "acc_card2")
    assert row["status"] == "paid"
    assert row["remaining_amount"] == 0.0


def test_card3_is_unpaid():
    row = _row(_call(), "acc_card3")
    assert row["status"] == "unpaid"
    assert row["remaining_amount"] == 300.0
    assert row["matched_payments"] == []
    assert any("no payment" in w.lower() for w in row["warnings"])


def test_card4_is_partial_and_overdue_with_654_remaining():
    row = _row(_call(), "acc_card4")
    assert row["status"] == "partial"
    assert row["remaining_amount"] == 654.0
    assert row["overdue"] is True
    assert row["days_until_due"] == -5
    assert row["matched_payments"] == [
        {"transaction_id": "t_card4_pay", "date": "2026-08-12", "amount": 346.0}
    ]


def test_card5_paid_with_elapsed_due_date_is_not_overdue():
    row = _row(_call(), "acc_card5")
    assert row["status"] == "paid"
    assert row["remaining_amount"] == 0.0
    assert row["days_until_due"] == -5
    assert row["overdue"] is False
    assert not any("overdue" in w.lower() for w in row["warnings"])


def test_card6_overpaid_clamps_remaining_to_zero_never_negative():
    """MATH-45: statement 200, payment 250 — the raw difference (200 - 250 = -50) must
    never surface as a negative "amount owed"."""
    row = _row(_call(), "acc_card6")
    assert row["status"] == "paid"
    assert row["remaining_amount"] == 0.0


def test_tool_remaining_amount_matches_service_field_for_every_card():
    """The tool must never re-derive "amount still owed" — it reads the ONE service field
    (MATH-45). Cross-checks every row (every status but 'unreconciled') against
    ``statement_summary().remaining_amount`` directly."""
    from backend.services.card_statements import statement_summary

    result = _call()
    for row in result["statements"]:
        if row["status"] == "unreconciled":
            assert row["remaining_amount"] is None
            continue
        summary = statement_summary(row["account_id"])
        assert summary is not None
        assert row["remaining_amount"] == summary.remaining_amount


def test_amex_is_missing_cycle():
    row = _row(_call(), "acc_amex")
    assert row["status"] == "missing-cycle"
    assert any("estimate" in w.lower() for w in row["warnings"])


def test_maybank_is_unreconciled():
    row = _row(_call(), "acc_maybank")
    assert row["status"] == "unreconciled"
    assert row["statement_amount"] is None
    assert row["evidence_ids"] == []


def test_no_account_id_returns_every_credit_card():
    result = _call()
    ids = {r["account_id"] for r in result["statements"]}
    assert ids == {
        "acc_card1",
        "acc_card2",
        "acc_card3",
        "acc_card4",
        "acc_amex",
        "acc_maybank",
        "acc_card5",
        "acc_card6",
    }
    assert result["total"] == 8
    assert result["showing"] == 8


def test_single_account_by_name():
    result = _call(account_id="Meridian Bank One")
    assert result["total"] == 1
    assert result["statements"][0]["account_id"] == "acc_card4"


def test_non_card_account_is_refused():
    result = _call(account_id="acc_checking")
    assert result["ok"] is False
    assert "credit" in result["error"]


def test_unknown_account_is_refused():
    result = _call(account_id="does-not-exist")
    assert result["ok"] is False
    assert "error" in result
