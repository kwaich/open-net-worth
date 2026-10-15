"""Regression: toggling a transaction's cleared flag must land in the audit trail.

BOARD inbox 2026-07-06: ``set_transaction_cleared`` called ``transactions.set_cleared()``
directly and returned — no ``record_txn_event`` — so reconciliation state changes had
zero provenance. The fix records ONE ``update`` event with a ``cleared`` before/after
diff, and only when the flag actually changes (a no-op toggle stays silent).

Covers the service choke point, the no-op case, the PATCH endpoint, and the AI tool
handler (both callers route through the same service function).
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.core import audit_context
from backend.services import audit
from backend.services import ledger as svc


@pytest.fixture
def env(tmp_path, monkeypatch, db_isolated):
    # accounts/transactions/audit/payees are DB-backed now (plan 3 task 4 / plan 4d task 2) —
    # db_isolated redirects every store instance (svc.transactions/.accounts, audit._audit/
    # ._txns, payee_registry._store) to the SAME fresh, empty database, so audit._txns and
    # the ledger's transactions already share storage without any per-instance path-sync.
    monkeypatch.setattr(svc, "base_currency", lambda: "SGD")
    svc.accounts.insert({"id": "acc_chk", "name": "Checking", "kind": "asset", "currency": "SGD"})
    audit_context.clear_audit_scope()
    yield tmp_path
    audit_context.clear_audit_scope()


def _make_txn():
    return svc.create_transaction(
        {
            "kind": "expense",
            "date": "2026-07-01",
            "description": "Anthropic, PBC",
            "from_account_id": "acc_chk",
            "from_amount": 11.5,
            "from_currency": "SGD",
        }
    )


def _update_events(txn_id: str) -> list[dict]:
    return [e for e in audit._audit.for_txn(txn_id) if e["op"] == "update"]


def test_toggle_records_one_update_event_with_cleared_diff(env):
    row = _make_txn()
    updated = svc.set_transaction_cleared(row["id"], True)
    assert updated["cleared"] is True
    events = _update_events(row["id"])
    assert len(events) == 1
    assert events[0]["before"] == {"cleared": False}
    assert events[0]["after"] == {"cleared": True}


def test_toggle_to_same_value_records_no_event(env):
    row = _make_txn()
    svc.set_transaction_cleared(row["id"], True)
    svc.set_transaction_cleared(row["id"], True)
    assert len(_update_events(row["id"])) == 1
    svc.set_transaction_cleared(row["id"], False)
    events = _update_events(row["id"])
    assert len(events) == 2
    assert events[1]["before"] == {"cleared": True}
    assert events[1]["after"] == {"cleared": False}


def test_untouched_row_toggled_false_records_no_event(env):
    row = _make_txn()
    assert not row.get("cleared")
    svc.set_transaction_cleared(row["id"], False)
    assert _update_events(row["id"]) == []


def test_missing_txn_returns_none_and_no_event(env):
    assert svc.set_transaction_cleared("ghost", True) is None
    assert audit._audit.for_txn("ghost") == []


def test_patch_cleared_endpoint_records_event(env):
    from backend.api.ledger import router as ledger_router

    app = FastAPI()
    app.include_router(ledger_router)
    client = TestClient(app)
    row = _make_txn()
    r = client.patch(f"/api/transactions/{row['id']}/cleared", json={"cleared": True})
    assert r.status_code == 200
    assert r.json()["cleared"] is True
    events = _update_events(row["id"])
    assert len(events) == 1
    assert events[0]["after"] == {"cleared": True}


def test_ai_tool_handler_records_event(env):
    import json as jsonlib

    from backend.ai.tools.accounts import set_transactions_cleared as tool

    row = _make_txn()
    text, _ = asyncio.run(tool({"txn_ids": [row["id"]], "cleared": True}))
    assert jsonlib.loads(text)["ok"] is True
    events = _update_events(row["id"])
    assert len(events) == 1
    assert events[0]["after"] == {"cleared": True}
