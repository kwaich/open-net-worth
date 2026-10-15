"""PAYEEPOST1 regression: POST /api/transactions silently dropped an explicit
``payee_id`` because ``TransactionCreate`` (backend/api/ledger.py) never declared the
field — pydantic's default ``model_dump()`` drops unknown keys, so the create choke
point (``services.ledger.create_transaction``) always fell through to description-text
auto-resolution instead. ``create_transaction`` already honors a caller-supplied
payee_id (validates it with ``payee_registry.require_payee`` and lets it WIN over the
description) — the fix is purely in the REST request schema + route error handling.

Offline + isolated: every store the paths touch is monkeypatched into tmp_path.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.ledger import router as ledger_router
from backend.services import ledger as svc
from backend.services import payee_registry


@pytest.fixture
def env(tmp_path, monkeypatch, db_isolated):
    # accounts/transactions are DB-backed (plan 3 task 4); payees are DB-backed too
    # (plan 4d task 2) — db_isolated redirects them all to a fresh, empty database.
    monkeypatch.setattr(svc, "base_currency", lambda: "SGD")
    svc.accounts.insert({"id": "acc_chk", "name": "Checking", "kind": "asset", "currency": "SGD"})
    payee = payee_registry.create_payee("Blue Bottle Coffee")
    return {"payee_id": payee["id"]}


@pytest.fixture
def client(env):
    app = FastAPI()
    app.include_router(ledger_router)
    return TestClient(app)


def _expense(**overrides):
    return {
        "kind": "expense",
        "date": "2026-07-01",
        "description": "some unrelated description text",
        "category": None,
        "from_account_id": "acc_chk",
        "from_amount": 6.5,
        "from_currency": "SGD",
        **overrides,
    }


def test_service_create_transaction_honors_explicit_payee_id(env):
    row = svc.create_transaction(_expense(payee_id=env["payee_id"]))
    assert row["payee_id"] == env["payee_id"]


def test_rest_post_transactions_honors_explicit_payee_id(env, client):
    resp = client.post("/api/transactions", json=_expense(payee_id=env["payee_id"]))
    assert resp.status_code == 200
    assert resp.json()["payee_id"] == env["payee_id"]


def test_rest_post_transactions_explicit_payee_wins_over_description_autoresolution(env, client):
    # The description text alone would resolve/mint a DIFFERENT payee than the explicit id —
    # the explicit payee_id must win, not the description.
    resp = client.post(
        "/api/transactions",
        json=_expense(description="Anthropic, PBC", payee_id=env["payee_id"]),
    )
    assert resp.status_code == 200
    assert resp.json()["payee_id"] == env["payee_id"]


def test_rest_post_transactions_omitted_payee_id_still_autoresolves_from_description(env, client):
    # Unchanged behavior: no explicit payee_id → auto-resolve/mint from the description.
    resp = client.post("/api/transactions", json=_expense(description="Anthropic, PBC"))
    assert resp.status_code == 200
    body = resp.json()
    assert body["payee_id"] is not None
    assert body["payee_id"] != env["payee_id"]


def test_rest_post_transactions_rejects_unknown_payee_id(env, client):
    resp = client.post("/api/transactions", json=_expense(payee_id="payee_does_not_exist"))
    assert resp.status_code == 404
    # Nothing was written under the hallucinated link.
    assert svc.transactions.query_txns() == []


# ── payee NAME input (import/AI: keep raw description, attach a clean merchant) ────────


def test_create_transaction_resolves_payee_name_keeps_raw_description(env):
    # An importer keeps the RAW descriptor in `description` (cross-source dedup) and
    # passes a CLEAN `payee` name — the choke mints/attaches that entity and never
    # persists the transient key.
    row = svc.create_transaction(_expense(description="MPOS*CINEBAR Q HOAN KIEM", payee="Cinebar"))
    assert row["description"] == "MPOS*CINEBAR Q HOAN KIEM"  # raw kept
    assert "payee" not in row  # transient key not stored as a column
    assert row["payee_id"]
    assert payee_registry.require_payee(row["payee_id"])["name"] == "Cinebar"


def test_explicit_payee_id_wins_over_payee_name(env):
    row = svc.create_transaction(
        _expense(description="raw", payee="Some Other Name", payee_id=env["payee_id"])
    )
    assert row["payee_id"] == env["payee_id"]
    assert "payee" not in row


def test_bulk_create_resolves_payee_name_keeps_raw_description(env):
    created, _ = svc.bulk_create_transactions(
        [_expense(description="fp*Kestrel Eats Singapore", payee="Kestrel Eats")]
    )
    row = created[0]
    assert row["description"] == "fp*Kestrel Eats Singapore"
    assert "payee" not in row
    assert payee_registry.require_payee(row["payee_id"])["name"] == "Kestrel Eats"
