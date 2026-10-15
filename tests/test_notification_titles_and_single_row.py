"""Notifications carry a real title, and an unattended write posts ONE in-app row.

Run:
    .venv/bin/python3 -m pytest tests/test_notification_titles_and_single_row.py -v
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from backend.ai import _confirm_gate
from backend.core import channels
from backend.db import NotificationLogStore
from backend.services.automations import executors


@pytest.fixture
def external(db_isolated):
    """The real in-app channel plus a recording stand-in for an external one."""
    import backend.services.notify  # noqa: F401  # registers the in-app channel at import

    saved = channels.registered_channels()
    sent: list[str] = []

    async def telegram(message):
        sent.append(message)
        return {"sent": 1}

    channels.register_channel("telegram", telegram)
    yield sent
    channels._CHANNELS.clear()
    channels._CHANNELS.update(saved)


def test_notify_automation_is_titled_with_its_name(external):
    automation = {"name": "Rent reminder", "execution": {"kind": "notify", "message": "Pay rent"}}
    asyncio.run(executors.execute_deterministic(automation))
    (row,) = NotificationLogStore().all()
    assert (row["title"], row["summary"]) == ("Rent reminder", "Pay rent")


def test_card_payment_reminder_has_a_title_and_no_repeated_prefix(external, monkeypatch):
    summary = SimpleNamespace(
        paid=False,
        partial=False,
        days_until_due=3,
        currency="USD",
        last_statement_balance=120.0,
        payment_amount=None,
    )
    monkeypatch.setattr("backend.services.card_statements.statement_summary", lambda _id: summary)
    automation = {
        "name": "Card guard",
        "execution": {
            "kind": "card_payment_guard",
            "card_account_id": "acc_1",
            "card_name": "Visa",
        },
    }
    result = asyncio.run(executors.execute_deterministic(automation))
    assert result["notified"], result
    (row,) = NotificationLogStore().all()
    assert row["title"] == "Payment reminder"
    assert row["summary"].startswith("Visa (USD 120.00) is due in 3 days")


def test_unattended_write_posts_one_in_app_row_and_still_reaches_external(external):
    asyncio.run(
        _confirm_gate.surface_unattended_write(
            agent_id="bot", tool_name="add_transactions", summary="Booked 1 transaction"
        )
    )
    assert [r["type"] for r in NotificationLogStore().all()] == ["review"]
    assert external == ["[bot] Booked 1 transaction"]


def test_unattended_refusal_posts_one_in_app_row_and_still_reaches_external(external):
    asyncio.run(
        _confirm_gate.surface_unattended_refusal(
            agent_id="bot",
            tool_name="delete_transactions",
            reason="outside envelope",
            attempted="delete",
        )
    )
    assert [r["type"] for r in NotificationLogStore().all()] == ["review"]
    assert external == ["[bot] refused — outside envelope"]
