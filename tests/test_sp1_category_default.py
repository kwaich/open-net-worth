"""Regression tests for SP1-1: the AI `add_transaction` category default.

Before the fix, `_txn_data_from_ai` hard-defaulted `category` to "other", which the
Automation engine treats as an explicit (protected) category — so a fill-only
(``override_existing=false``) transform was a no-op on AI-added transactions.

After the fix the AI mapper passes `category=None` when the user didn't name one, and
`services.ledger.create_transaction` leaves it as None when no Automation or payee default applies.
This:
  1. lets a fill-only Automation categorize an AI-added expense,
  2. preserves a user-chosen category against a fill-only Automation,
  3. leaves category=None (Uncategorized) when nothing can classify it.

Offline + isolated: stores are monkeypatched into tmp_path; execute_tool is async,
driven via asyncio.run. No LLM, no network.

Run:
    .venv/bin/pytest tests/test_sp1_category_default.py -v
"""

from __future__ import annotations

import asyncio
import json

import pytest

from backend.ai.tools import execute_tool
from backend.db.automation_store import AutomationStore
from backend.services import ledger as svc


@pytest.fixture
def isolated(tmp_path, monkeypatch, db_isolated):
    """Point every store the add_transaction path touches at tmp files; base=SGD.
    accounts/transactions/automations are DB-backed (plan 3 task 4 / plan 4d task 2) —
    db_isolated covers those."""
    monkeypatch.setattr(svc, "base_currency", lambda: "SGD")
    acct = svc.accounts.create(
        {
            "name": "Meridian Bank",
            "kind": "asset",
            "type": "cash",
            "currency": "SGD",
            "initial_balance": 0,
        }
    )
    return {"tmp": tmp_path, "account_id": acct["id"]}


def call(tool, **inp):
    text, proposed = asyncio.run(execute_tool(tool, inp))
    return json.loads(text), proposed


def _create_grab_fill_automation():
    """Create a default fill-only categorization Automation."""
    return AutomationStore().create(
        {
            "name": "Grab → transport",
            "status": "active",
            "trigger": {
                "kind": "event",
                "event": "txn",
                "when": {"description_contains": "grab"},
                "limit": "each",
            },
            "execution": {
                "kind": "transaction_transform",
                "set": {"category": "cat_db5642ab0000"},
                "override_existing": False,
            },
        }
    )


# ── case 1: no category + a fill-only rule that matches → rule fills it ──────────


# Removed 2026-08-02: these pinned a DECLARATIVE transaction transform categorizing the
# row inside create_transaction. Rules are event-triggered Automations now — they run
# AFTER the commit — so there is nothing to observe at the write. The surviving
# attribution is pinned in tests/test_rule_audit_provenance.py.


# ── case 2: no category + NO matching rule → None (Uncategorized) ──


def test_ai_add_no_category_no_automation_stays_none(isolated):
    res, _ = call(
        "add_transactions",
        description="Some random merchant",
        amount=9.0,
        currency="SGD",
        account_id=isolated["account_id"],
        # NO category, and no Automation exists
    )
    assert res["ok"] is True, res
    # Blank expense stays None (Uncategorized) — no forced "uncategorized" sink.
    assert res["transaction"]["category"] is None


# ── case 3: explicit user category + a fill-only rule → user category preserved ──
