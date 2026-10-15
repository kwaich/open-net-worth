"""Tests for ``services.ledger.mark_batch_reviewed`` + its REST endpoint
(``POST /api/transactions/batches/{batch_id}/mark-reviewed``) — the "Mark all as reviewed"
action on the Statement Import Done screen (SI-79).

Clears ``needs_review`` on every non-deleted transaction CREATED in a given import batch, via
the existing ``bulk_update_transactions`` choke — never a raw store write. A merged/linked row
was never tagged with the batch's ``batch_id`` (only a newly created row carries it), so
it must be untouched by this call.

Offline + isolated: real ``TransactionStore``/``ImportBatchStore``/``AccountStore`` instances
repointed at tmp_path; rules are off and the correction recorder is stubbed, mirroring
``tests/test_bulk_transactions_api.py``'s fixture — no network, no live data.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.ledger import router as ledger_router
from backend.core.errors import NotFoundError
from backend.db.account_store import AccountStore
from backend.db.import_batch_store import ImportBatchStore
from backend.db.transaction_store import TransactionStore
from backend.services import ledger as svc


@pytest.fixture
def stores(tmp_path, monkeypatch):
    acc_store = AccountStore()
    txn_store = TransactionStore()
    batch_store = ImportBatchStore()
    acc_store._path = tmp_path / "accounts.json"
    txn_store._path = tmp_path / "transactions.json"
    batch_store._path = tmp_path / "import_batches.json"
    monkeypatch.setattr(svc, "accounts", acc_store)
    monkeypatch.setattr(svc, "transactions", txn_store)
    monkeypatch.setattr(svc, "import_batches", batch_store)
    monkeypatch.setattr(svc, "base_currency", lambda: "SGD")
    monkeypatch.setattr(svc, "to_base", lambda amt, cur, base=None, rates=None: round(amt or 0, 2))

    from backend.services import corrections as corrections_service

    monkeypatch.setattr(corrections_service, "record_correction", lambda old, new, **kw: None)
    return acc_store, txn_store, batch_store


@pytest.fixture
def client(stores):
    app = FastAPI()
    app.include_router(ledger_router)
    return TestClient(app)


def _seed_batch_and_txns(acc_store, txn_store, batch_store):
    """A batch with 2 newly-created rows (needs_review=True) + one pre-existing/merged row
    that carries NO batch_id and must never be touched."""
    acct = acc_store.create(
        {"name": "Meridian Bank", "kind": "asset", "type": "cash", "currency": "SGD"}
    )
    batch = batch_store.create_batch(
        "Statement Apr 2026", transaction_count=2, source_account_id=acct["id"]
    )
    created_rows = [
        txn_store.append(
            {
                "date": "2026-04-0" + str(i + 1),
                "kind": "expense",
                "category": "cat_13095ba70000",
                "from_account_id": acct["id"],
                "from_amount": 12.0 * (i + 1),
                "from_currency": "SGD",
                "batch_id": batch["id"],
                "needs_review": True,
            }
        )
        for i in range(2)
    ]
    merged_row = txn_store.append(
        {
            "date": "2026-03-20",
            "kind": "expense",
            "category": "cat_a6b4e3bc0000",
            "from_account_id": acct["id"],
            "from_amount": 50.0,
            "from_currency": "SGD",
            "needs_review": True,
        }
    )
    return batch, created_rows, merged_row


# ── Service-level ──────────────────────────────────────────────────────────────


def test_mark_batch_reviewed_clears_only_the_batchs_rows(stores):
    acc_store, txn_store, batch_store = stores
    batch, created_rows, merged_row = _seed_batch_and_txns(acc_store, txn_store, batch_store)

    result = svc.mark_batch_reviewed(batch["id"])

    assert result == {"updated": 2}
    for r in created_rows:
        refreshed = txn_store.find_by_id(r["id"])
        assert refreshed["needs_review"] is False
    still = txn_store.find_by_id(merged_row["id"])
    assert still["needs_review"] is True, "a row outside the batch must never be re-flagged"


def test_mark_batch_reviewed_is_a_noop_when_nothing_needs_it(stores):
    acc_store, txn_store, batch_store = stores
    acct = acc_store.create(
        {"name": "Meridian Bank", "kind": "asset", "type": "cash", "currency": "SGD"}
    )
    batch = batch_store.create_batch("Statement", transaction_count=1, source_account_id=acct["id"])
    txn_store.append(
        {
            "date": "2026-04-01",
            "kind": "expense",
            "category": "cat_13095ba70000",
            "from_account_id": acct["id"],
            "from_amount": 10.0,
            "from_currency": "SGD",
            "batch_id": batch["id"],
            "needs_review": False,
        }
    )

    result = svc.mark_batch_reviewed(batch["id"])

    assert result == {"updated": 0}


def test_mark_batch_reviewed_unknown_batch_raises_not_found(stores):
    with pytest.raises(NotFoundError):
        svc.mark_batch_reviewed("imp_does_not_exist")


# ── REST endpoint ────────────────────────────────────────────────────────────


def test_mark_reviewed_endpoint_clears_batch_rows(client, stores):
    acc_store, txn_store, batch_store = stores
    batch, created_rows, merged_row = _seed_batch_and_txns(acc_store, txn_store, batch_store)

    r = client.post(f"/api/transactions/batches/{batch['id']}/mark-reviewed")

    assert r.status_code == 200
    assert r.json() == {"updated": 2}
    for row in created_rows:
        assert txn_store.find_by_id(row["id"])["needs_review"] is False
    assert txn_store.find_by_id(merged_row["id"])["needs_review"] is True


def test_mark_reviewed_endpoint_unknown_batch_404(client):
    r = client.post("/api/transactions/batches/imp_does_not_exist/mark-reviewed")
    assert r.status_code == 404
