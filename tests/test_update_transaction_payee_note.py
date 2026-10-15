"""PAYEE1 investigation finding: `update_transaction` never auto-resolves or auto-creates
a payee from description text — that "ride the description, the backend resolves/learns the
payee" convention only ever applies to a genuine CREATE (`create_transaction`,
`_skip_payee_resolve` defaults False). `update_transaction` re-runs the create logic with
`_skip_payee_resolve=True` specifically so an edit never re-derives the payee link from
whatever the description happens to say (backend/services/ledger.py, `create_transaction`'s
`_skip_payee_resolve` branch: "Never auto-resolve from the description on an edit").

This is the backend evidence behind the PAYEE1 fix: the transaction-detail drawer
(`AccountsTab.saveDetailEdit`) and the inline register-cell edit (`updateFromPatch`) cannot
rely on riding a new payee name into `description` on save — they must explicitly create the
payee (`POST /api/payees`) and attach the real returned id. Offline + isolated: all stores
redirect to tmp_path; no network, no live data files.
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
    # accounts/transactions are DB-backed now (plan 3 task 4); payees are DB-backed too
    # (plan 4d task 2) — db_isolated redirects them all to a fresh, empty database.
    monkeypatch.setattr(svc, "base_currency", lambda: "SGD")
    svc.accounts.insert({"id": "acc_chk", "name": "Checking", "kind": "asset", "currency": "SGD"})
    return tmp_path


@pytest.fixture
def client(env):
    app = FastAPI()
    app.include_router(ledger_router)
    return TestClient(app)


def _blank_expense():
    return {
        "kind": "expense",
        "date": "2026-06-30",
        "description": "",
        "category": None,
        "from_account_id": "acc_chk",
        "from_amount": 11.5,
        "from_currency": "SGD",
    }


def test_service_update_does_not_resolve_payee_from_description(env):
    row = svc.create_transaction(_blank_expense())
    assert row["payee_id"] is None  # blank description on create → nothing to resolve either
    before_count = len(payee_registry._store.all())

    updated = svc.update_transaction(row["id"], {"description": "Cousin - A"})

    assert updated["payee_id"] is None  # the edit never re-derives a payee from the description
    assert len(payee_registry._store.all()) == before_count  # no payee minted by the edit


def test_service_update_preserves_existing_payee_when_description_changes(env):
    # A transaction that already resolved a payee on CREATE must keep that link on an
    # unrelated description edit — an edit never re-runs payee resolution at all.
    row = svc.create_transaction({**_blank_expense(), "description": "Anthropic, PBC"})
    pid = row["payee_id"]
    assert pid is not None

    updated = svc.update_transaction(row["id"], {"description": "Anthropic, PBC — annual renewal"})

    assert updated["payee_id"] == pid


def test_api_put_transactions_does_not_create_payee_from_description(client):
    created = client.post("/api/transactions", json=_blank_expense())
    assert created.status_code == 200
    row = created.json()
    assert row["payee_id"] is None
    before_count = len(payee_registry._store.all())

    updated = client.put(f"/api/transactions/{row['id']}", json={"description": "Cousin - A"})

    assert updated.status_code == 200
    assert updated.json()["payee_id"] is None
    assert len(payee_registry._store.all()) == before_count
