"""Regression test for SP4-2: Lucius `update_transaction` tag parity.

`TransactionUpdate` + `PUT /api/transactions/{id}` already support replacing `tags`,
but the AI `update_transaction` tool omitted `tags` from both its input_schema and the
copied `fields` whitelist — so Lucius could set tags on create but not edit them on an
existing transaction. The fix adds `tags` to the schema + the fields dict.

Offline + isolated: stores are monkeypatched into tmp_path; execute_tool is async,
driven via asyncio.run. No rules → tags persist exactly as supplied (rules only union).
No LLM, no network.

Run:
    .venv/bin/pytest tests/test_sp4_update_tags.py -v
"""

from __future__ import annotations

import asyncio
import json

import pytest

from backend.ai.tools import TOOL_SCHEMAS, execute_tool
from backend.db.category_store import CategoryStore
from backend.models.category_defs import BUILTIN_CATEGORIES
from backend.services import ledger as svc


@pytest.fixture
def isolated(tmp_path, monkeypatch, db_isolated):
    monkeypatch.setattr(svc, "base_currency", lambda: "SGD")
    # CategoryStore is DB-backed (plan 4a task 2); db_isolated redirects it to a fresh, empty
    # database — reseed BUILTIN_CATEGORIES here (tests/conftest.py's shared autouse seed ran
    # BEFORE db_isolated activated, so it landed in the old, now-orphaned default engine).
    CategoryStore().replace_all(list(BUILTIN_CATEGORIES))
    acct = svc.accounts.create(
        {
            "name": "Meridian Bank",
            "kind": "asset",
            "type": "cash",
            "currency": "SGD",
            "initial_balance": 0,
        }
    )
    txn = svc.create_transaction(
        {
            "date": "2026-06-04",
            "kind": "expense",
            "description": "Grab ride",
            "category": "transport",
            "from_account_id": acct["id"],
            "from_amount": 18.0,
            "from_currency": "SGD",
            "tags": ["old"],
        }
    )
    return {"tmp": tmp_path, "account_id": acct["id"], "txn_id": txn["id"]}


def call(tool, **inp):
    text, proposed = asyncio.run(execute_tool(tool, inp))
    return json.loads(text), proposed


def test_update_transaction_schema_offers_tags():
    schema = next(t for t in TOOL_SCHEMAS if t["name"] == "update_transactions")
    item_props = schema["input_schema"]["properties"]["updates"]["items"]["properties"]
    assert "tags" in item_props


def test_update_transaction_edits_tags(isolated):
    res, proposed = call(
        "update_transactions",
        transaction_id=isolated["txn_id"],
        tags=["vietnam", "commute"],
    )
    assert res["ok"] is True, res
    assert proposed is None
    # Tags replaced with the supplied list (rules engine has no rules to union).
    assert res["transaction"]["tags"] == ["vietnam", "commute"]

    # Persisted, not just echoed.
    stored = svc.transactions.find_by_id(isolated["txn_id"])
    assert stored["tags"] == ["vietnam", "commute"]
