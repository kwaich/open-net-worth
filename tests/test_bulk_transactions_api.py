"""API smoke tests for the bulk transaction endpoints (multi-select register).

  - PATCH /api/transactions/bulk      — set category / add tags / mark cleared.
  - POST  /api/transactions/bulk-delete — soft-delete by explicit id list.

Offline + isolated: a minimal FastAPI app mounts only the ledger router; the account +
transaction stores are repointed at tmp_path; rules are off and the correction recorder
is stubbed, so requests never touch live data.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.ledger import router as ledger_router
from backend.db.account_store import AccountStore
from backend.db.transaction_store import TransactionStore
from backend.services import ledger as svc


@pytest.fixture
def client(tmp_path, monkeypatch):
    acc_store = AccountStore()
    txn_store = TransactionStore()
    acc_store._path = tmp_path / "accounts.json"
    txn_store._path = tmp_path / "transactions.json"
    monkeypatch.setattr(svc, "accounts", acc_store)
    monkeypatch.setattr(svc, "transactions", txn_store)
    monkeypatch.setattr(svc, "base_currency", lambda: "SGD")
    monkeypatch.setattr(svc, "to_base", lambda amt, cur, base=None, rates=None: round(amt or 0, 2))

    from backend.services import corrections as corrections_service

    monkeypatch.setattr(corrections_service, "record_correction", lambda old, new, **kw: None)

    app = FastAPI()
    app.include_router(ledger_router)
    return TestClient(app), acc_store, txn_store


def _seed(acc_store, txn_store, n=2):
    acct = acc_store.create(
        {"name": "Meridian Bank", "kind": "asset", "type": "cash", "currency": "SGD"}
    )
    rows = [
        txn_store.append(
            {
                "date": "2026-02-01",
                "kind": "expense",
                "category": "cat_a6b4e3bc0000",
                "from_account_id": acct["id"],
                "from_amount": 10.0 * (i + 1),
                "from_currency": "SGD",
            }
        )
        for i in range(n)
    ]
    return acct, rows


def test_bulk_patch_sets_category_and_tags(client):
    cl, acc_store, txn_store = client
    _, rows = _seed(acc_store, txn_store)
    ids = [r["id"] for r in rows]
    r = cl.patch(
        "/api/transactions/bulk",
        json={"ids": ids, "changes": {"category": "cat_13095ba70000", "tags": ["work"]}},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 2 and body["not_found"] == []
    for tid in ids:
        row = txn_store.find_by_id(tid)
        assert row["category"] == "cat_13095ba70000" and "work" in row["tags"]


def test_bulk_patch_unknown_category_422(client):
    cl, acc_store, txn_store = client
    _, rows = _seed(acc_store, txn_store, n=1)
    r = cl.patch(
        "/api/transactions/bulk",
        json={"ids": [rows[0]["id"]], "changes": {"category": "nope-not-real"}},
    )
    assert r.status_code == 422


def test_bulk_patch_empty_ids_422(client):
    cl, _, _ = client
    r = cl.patch("/api/transactions/bulk", json={"ids": [], "changes": {"cleared": True}})
    assert r.status_code == 422


def test_bulk_delete_by_ids(client):
    cl, acc_store, txn_store = client
    _, rows = _seed(acc_store, txn_store)
    ids = [r["id"] for r in rows]
    r = cl.post("/api/transactions/bulk-delete", json={"ids": ids})
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 2 and body["not_found"] == []
    for tid in ids:
        row = txn_store.find_by_id(tid)
        assert row is None or row.get("deleted")


def test_bulk_delete_empty_ids_422(client):
    cl, _, _ = client
    r = cl.post("/api/transactions/bulk-delete", json={"ids": []})
    assert r.status_code == 422
