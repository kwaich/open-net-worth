"""Regression for the BUD-SWEEP-01 follow-up (EXTRA B, 2026-07-04): a broader audit of every
JsonStore subclass found several MORE bespoke mutators doing an UNLOCKED read-modify-write of
the whole file, beyond the BudgetAssignmentsStore.set_assigned bug already fixed
(tests/test_budget_assignments_race.py). The most money-critical one is
``TransactionStore.bulk_set_deleted`` — the core ledger every dollar figure in the app is
computed from — which used to silently lose a concurrent soft-delete/undelete under two
threads racing the same file (last-writer-wins, exactly the assignments bug's shape)."""

from __future__ import annotations

import threading

from backend.db.transaction_store import TransactionStore
from tests.conftest import seed_stub_accounts


def _txn(i: int) -> dict:
    return {
        "id": f"t{i}",
        "date": "2026-06-01",
        "kind": "expense",
        "category": "food",
        "from_account_id": "bank",
        "from_amount": 10.0 + i,
        "from_currency": "SGD",
        "deleted": False,
    }


def test_concurrent_bulk_set_deleted_loses_no_updates(tmp_path, monkeypatch, db_isolated):
    store = TransactionStore()

    n = 12
    txns = [_txn(i) for i in range(n)]
    seed_stub_accounts(txns)
    store.bulk_insert(txns)

    barrier = threading.Barrier(n)

    def worker(i: int) -> None:
        barrier.wait()
        store.bulk_set_deleted([f"t{i}"], True)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    items = store.all()
    deleted_ids = {t["id"] for t in items if t.get("deleted")}
    assert deleted_ids == {f"t{i}" for i in range(n)}, (
        f"lost {n - len(deleted_ids)} delete(s): {sorted(deleted_ids)}"
    )
