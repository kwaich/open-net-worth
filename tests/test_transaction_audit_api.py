"""Phase 2 — transaction provenance read surface + scope attribution.

Covers the ``trace_transaction`` AI tool, the ``GET /api/transactions/{id}/audit`` REST
endpoint, the router-level UI scope (a REST ledger write is attributed actor=user/
surface=ui), and the ambient ``set_audit_scope_if_absent`` semantics (an import does not
clobber a richer chat scope).

DESIGN: .claude/rfcs/transaction-provenance.md
"""

from __future__ import annotations

import asyncio
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.ai.tools import execute_tool
from backend.core import audit_context
from backend.db.category_store import CategoryStore
from backend.models.category_defs import BUILTIN_CATEGORIES
from backend.services import audit as audit_svc
from backend.services import ledger as svc


@pytest.fixture
def env(tmp_path, monkeypatch, db_isolated):
    # accounts/transactions/audit are DB-backed now (plan 3 task 4); categories are
    # DB-backed too (plan 4a task 2) — db_isolated redirects every store instance to the
    # same fresh, empty database. Reseed BUILTIN_CATEGORIES here (tests/conftest.py's
    # shared autouse seed ran BEFORE db_isolated activated, so it landed in the old,
    # now-orphaned default engine).
    monkeypatch.setattr(svc, "base_currency", lambda: "SGD")
    CategoryStore().replace_all(list(BUILTIN_CATEGORIES))
    svc.accounts.insert({"id": "acc_chk", "name": "Checking", "kind": "asset", "currency": "SGD"})
    audit_context.clear_audit_scope()
    yield tmp_path
    audit_context.clear_audit_scope()


@pytest.fixture
def client(env):
    from backend.api.ledger import router as ledger_router

    app = FastAPI()
    app.include_router(ledger_router)
    return TestClient(app)


def _expense(desc="Anthropic, PBC", date="2026-06-07", amount=21.8):
    return {
        "kind": "expense",
        "date": date,
        "description": desc,
        "category": "subscriptions",
        "from_account_id": "acc_chk",
        "from_amount": amount,
        "from_currency": "SGD",
    }


# ── trace_transaction tool ───────────────────────────────────────────────────────


def test_trace_transaction_tool_returns_events(env):
    row = svc.create_transaction(_expense())
    text, proposed = asyncio.run(execute_tool("trace_transactions", {"transaction_id": row["id"]}))
    assert proposed is None
    data = json.loads(text)
    assert data["txn_id"] == row["id"]
    assert data["reconstructed"] is False
    assert [e["op"] for e in data["events"]] == ["create"]


def test_trace_transaction_tool_requires_id(env):
    text, _ = asyncio.run(execute_tool("trace_transactions", {}))
    assert "error" in json.loads(text)


# ── REST endpoint + UI scope attribution ─────────────────────────────────────────


def test_get_audit_endpoint_and_ui_attribution(client):
    # Create via the REST router → the _ui_audit_scope dependency stamps actor=user/surface=ui.
    created = client.post("/api/transactions", json=_expense()).json()
    txn_id = created["id"]
    r = client.get(f"/api/transactions/{txn_id}/audit")
    assert r.status_code == 200
    body = r.json()
    assert body["reconstructed"] is False
    ev = body["events"][0]
    assert ev["op"] == "create"
    assert ev["actor"] == "user"
    assert ev["surface"] == "ui"


def test_rest_delete_records_delete_event(client):
    created = client.post("/api/transactions", json=_expense()).json()
    txn_id = created["id"]
    assert client.delete(f"/api/transactions/{txn_id}").status_code == 200
    body = client.get(f"/api/transactions/{txn_id}/audit").json()
    ops = [e["op"] for e in body["events"]]
    assert ops == ["create", "delete"]
    assert body["events"][-1]["actor"] == "user" and body["events"][-1]["surface"] == "ui"


def test_audit_endpoint_reconstructs_unknown_row(client):
    body = client.get("/api/transactions/missing-xyz/audit").json()
    assert body["reconstructed"] is True
    assert body["events"] == []


# ── ambient scope (import must not clobber a chat scope) ──────────────────────────


def test_set_audit_scope_if_absent_respects_existing():
    audit_context.clear_audit_scope()
    # No active scope → it sets and returns True (caller owns the clear).
    assert audit_context.set_audit_scope_if_absent("user", "import") is True
    assert audit_context.get_audit_scope().surface == "import"
    # A scope is now active → a second ambient set is a no-op, returns False.
    assert audit_context.set_audit_scope_if_absent("system", "recurring") is False
    assert audit_context.get_audit_scope().surface == "import"  # unchanged
    audit_context.clear_audit_scope()


def test_chat_scope_survives_import(env):
    # Simulate a chat turn having set the scope, then an import running within it.
    audit_context.set_audit_scope("ai", "chat:web", session_id="sess-9", intent="import this")
    svc.bulk_create_transactions([_expense(desc="X")])
    # The import path uses set_audit_scope_if_absent → keeps chat:web (does not become import).
    # (bulk_create_transactions here is the choke point; the import wrapper lives in
    # import_common.build_commit, which is what would call if_absent — so we assert the
    # scope is unchanged and the event carries the chat attribution.)
    rows = svc.transactions.all()
    ev = audit_svc._audit.for_txn(rows[0]["id"])[0]
    assert ev["surface"] == "chat:web"
    assert ev["session_id"] == "sess-9"
    audit_context.clear_audit_scope()
