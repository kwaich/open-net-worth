"""
Recorder integration / regression test for learn-from-corrections (RFC P2-SP2).

This is the load-bearing regression that proves the loop: editing a transaction's
CATEGORY via PUT /api/transactions/{id} writes exactly one correction event at the
single edit choke point (api/ledger.update_transaction); editing only the description/amount
writes NONE; three category edits of the same payee make corrections.suggestions()
return one card.

Offline + isolated: a minimal FastAPI app mounts only the ledger router; the account,
transaction, correction, Automation, and profile stores are monkeypatched into tmp_path so
requests never touch live data. No LLM, no network.

Run:
    .venv/bin/pytest tests/test_corrections_recorder.py -v
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import backend.api.ledger as ledger
from backend.db.automation_store import AutomationStore
from backend.db.category_store import CategoryStore
from backend.db.correction_store import CorrectionStore
from backend.models.category_defs import BUILTIN_CATEGORIES
from backend.services import corrections as corrections_service
from backend.services import ledger as svc


@pytest.fixture
def client(tmp_path, db_isolated):
    # CategoryStore is DB-backed (plan 4a task 2); db_isolated redirects it to a fresh, empty
    # database — reseed BUILTIN_CATEGORIES here (the shared tests/conftest.py autouse seed ran
    # BEFORE db_isolated activated, so it landed in the old, now-orphaned default engine) so
    # the update_transaction choke's unknown-category check resolves the ids this file uses.
    CategoryStore().replace_all(list(BUILTIN_CATEGORIES))
    app = FastAPI()
    app.include_router(ledger.router)
    return TestClient(app), tmp_path


def _seed_account():
    return svc.accounts.insert(
        {"id": "acc1", "name": "Bank", "kind": "asset", "currency": "SGD", "initial_balance": 0}
    )


def _seed_expense(description="STARBUCKS", category="cat_6c4bc0170000"):
    """An existing expense txn (seeded directly via the store, bypassing rules)."""
    return svc.transactions.append(
        {
            "date": "2026-06-01",
            "kind": "expense",
            "description": description,
            "category": category,
            "from_account_id": "acc1",
            "from_amount": 6.0,
            "from_currency": "SGD",
            "base_amount": 6.0,
            "base_currency": "SGD",
            "tags": [],
        }
    )


def test_category_edit_records_one_correction(client):
    c, _ = client
    _seed_account()
    txn = _seed_expense(category="cat_6c4bc0170000")
    r = c.put(f"/api/transactions/{txn['id']}", json={"category": "cat_13095ba70000"})
    assert r.status_code == 200
    assert r.json()["category"] == "cat_13095ba70000"
    events = CorrectionStore().events()
    assert len(events) == 1
    assert events[0]["payee_key"] == "starbucks"
    assert events[0]["from_category"] == "cat_6c4bc0170000"
    assert events[0]["to_category"] == "cat_13095ba70000"
    assert events[0]["txn_id"] == txn["id"]


def test_correction_records_payee_id(client):
    # ADR 0010: a correction carries the resolved payee_id so it generalizes across every
    # description variant of the payee (not just the one normalized string).
    c, _ = client
    _seed_account()
    created = svc.create_transaction(
        {
            "kind": "expense",
            "date": "2026-06-01",
            "description": "Anthropic, PBC",
            "category": "cat_f2ff09940000",
            "from_account_id": "acc1",
            "from_amount": 14.06,
            "from_currency": "SGD",
        }
    )
    pid = created["payee_id"]
    assert pid
    r = c.put(f"/api/transactions/{created['id']}", json={"category": "cat_13095ba70000"})
    assert r.status_code == 200
    events = CorrectionStore().events()
    assert events[-1]["payee_id"] == pid


def test_memo_only_edit_records_no_correction(client):
    c, _ = client
    _seed_account()
    txn = _seed_expense(category="cat_13095ba70000")
    r = c.put(f"/api/transactions/{txn['id']}", json={"memo": "with a friend"})
    assert r.status_code == 200
    # category unchanged → not a correction
    assert CorrectionStore().events() == []


def test_amount_only_edit_records_no_correction(client):
    c, _ = client
    _seed_account()
    txn = _seed_expense(category="cat_13095ba70000")
    r = c.put(f"/api/transactions/{txn['id']}", json={"from_amount": 9.0})
    assert r.status_code == 200
    assert CorrectionStore().events() == []


def test_three_category_edits_make_one_suggestion(client):
    c, _ = client
    _seed_account()
    # Three separate STARBUCKS transactions, each recategorized shopping → food.
    for _ in range(3):
        txn = _seed_expense(description="STARBUCKS @ ION", category="cat_6c4bc0170000")
        r = c.put(f"/api/transactions/{txn['id']}", json={"category": "cat_13095ba70000"})
        assert r.status_code == 200

    assert len(CorrectionStore().events()) == 3
    suggestions = corrections_service.suggestions()
    assert len(suggestions) == 1
    s = suggestions[0]
    assert s["payee_key"] == "starbucks ion"
    assert s["target_category"] == "cat_13095ba70000"
    assert s["count"] == 3


def test_a_matching_automation_no_longer_suppresses_the_suggestion(client):
    """This asserted suppression until 2026-08-02, when categorization rules became
    event-triggered Automations.

    A rule used to be a declarative row this service could match a payee against, so a
    learned suggestion could stand down where a rule already categorized. A rule is now a
    Lucius run that judges the match itself, AFTER the row commits — nothing here can
    answer "would an Automation handle this?" without running the model, so the suppressor
    is gone (services/categorize._covered_by_enabled_rule).

    The cost is a redundant suggestion where an Automation would also have acted. That is
    the right side to be wrong on: a suggestion is offered, never applied.
    """
    c, _ = client
    _seed_account()
    for _ in range(3):
        txn = _seed_expense(description="STARBUCKS", category="cat_6c4bc0170000")
        c.put(f"/api/transactions/{txn['id']}", json={"category": "cat_13095ba70000"})
    assert len(corrections_service.suggestions()) == 1

    AutomationStore().create(
        {
            "name": "starbucks → food",
            "status": "active",
            "instructions": "Categorize matching transactions.",
            "trigger": {
                "kind": "event",
                "event": "txn",
                "when": {"description_contains": "starbucks", "operation": "create"},
                "limit": "each",
            },
            "execution": {"kind": "lucius"},
        }
    )

    assert len(corrections_service.suggestions()) == 1
