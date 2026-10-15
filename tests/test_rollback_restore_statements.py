"""
Regression tests for rollback_import_batch / restore_import_batch / verify_statement_balance
fixes (lane/rollback-restore).

FIX A: rollback_import_batch destructive-action guard
  - preview:true returns summary, deletes nothing
  - preview on a batch older than 24h includes a 'warning' key
  - a plain call deletes transactions and returns summary + deleted count

FIX B: restore_import_batch (undo rollback)
  - rollback → restore round-trip restores all transactions and sets status back to 'active'
  - restore on a non-rolled-back batch is refused

FIX C: verify_statement_balance mismatch diagnostics
  - mismatch includes largest_legs (top 3 by abs amount)
  - hint appears when a leg is >50× the expected net
  - hint is absent when amounts are sane
  - account_currency is always present in the result
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from backend.db.import_batch_store import ImportBatchStore
from backend.db.transaction_store import TransactionStore
from backend.services import ledger as svc

# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture()
def isolated_stores(tmp_path: Path, monkeypatch, db_isolated):
    """Redirect transaction and import-batch stores onto the per-test isolated database;
    also stub accounts."""
    from tests.conftest import seed_stub_accounts

    monkeypatch.setattr(svc, "transactions", TransactionStore())
    monkeypatch.setattr(svc, "import_batches", ImportBatchStore())
    # A real stub row so from_account_id="acc-vnd" satisfies the transactions FK.
    seed_stub_accounts([{"from_account_id": "acc-vnd"}])
    # Stub accounts so verify_statement_balance can find our synthetic account
    monkeypatch.setattr(
        svc.accounts,
        "find_by_id",
        lambda aid: {"id": aid, "currency": "VND"} if aid == "acc-vnd" else None,
    )


def _create_batch(
    age_hours: float = 0,
    count: int = 3,
    status: str = "active",
) -> dict:
    """Helper: insert a batch and its transactions into the isolated stores."""
    created_at = (datetime.now(timezone.utc) - timedelta(hours=age_hours)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    batch = {
        "id": str(uuid.uuid4()),
        "created_at": created_at,
        "description": "Test batch",
        "status": status,
        "transaction_count": count,
        "source_account_id": "acc-vnd",
    }
    batch = svc.import_batches.insert(batch)
    for i in range(count):
        txn = {
            "id": str(uuid.uuid4()),
            "date": f"2026-05-{i + 1:02d}",
            "kind": "expense",
            "description": f"Txn {i + 1}",
            "from_account_id": "acc-vnd",
            "from_amount": float(100_000 * (i + 1)),
            "from_currency": "VND",
            "to_account_id": None,
            "to_amount": None,
            "to_currency": None,
            "category": "food",
            "base_amount": float(100 * (i + 1)),
            "base_currency": "SGD",
            "fx_rate": 1000.0,
            "batch_id": batch["id"],
            "deleted": False,
            "tags": [],
            "splits": None,
            "cleared": False,
            "source": "ai",
            "external_refs": [],
            "merged_from": [],
            "merged_into": None,
            "note": "",
            "payee_id": None,
            "ticker": None,
            "quantity": None,
            "created_at": created_at,
        }
        svc.transactions.insert(txn)
    return batch


def call(tool_name: str, **inp):
    """Execute a registered tool synchronously."""
    from backend.ai.tools import execute_tool

    text, proposed = asyncio.run(execute_tool(tool_name, inp))
    return json.loads(text), proposed


# ── FIX A: rollback_import_batch preview ─────────────────────────────────────


def test_rollback_preview_returns_summary_without_deleting(isolated_stores):
    """preview:true must return summary and delete nothing."""
    batch = _create_batch(age_hours=1, count=4)
    resp, _ = call("rollback_import_batch", batch_id=batch["id"], preview=True)

    assert resp["ok"] is True
    assert resp["preview"] is True
    assert "batch" in resp
    assert resp["batch"]["id"] == batch["id"]
    assert resp["batch"]["transaction_count"] == 4
    # date_span must be present
    assert resp["batch"]["date_span"]["from"] is not None
    # sample must have ≤5 rows
    assert len(resp["batch"]["sample"]) <= 5
    # No transactions should be soft-deleted
    live = svc.transactions.query_by_batch(batch["id"], include_deleted=False)
    assert len(live) == 4, "preview must NOT delete any transactions"
    # Batch status must be unchanged
    refreshed = svc.import_batches.find_by_id(batch["id"])
    assert refreshed["status"] == "active"


def test_rollback_preview_includes_warning_for_old_batch(isolated_stores):
    """Preview on a batch older than 24h must include a 'warning' key."""
    batch = _create_batch(age_hours=25, count=2)
    resp, _ = call("rollback_import_batch", batch_id=batch["id"], preview=True)

    assert resp["ok"] is True
    assert resp["preview"] is True
    assert "warning" in resp, "old batch must include warning"
    # No deletion
    live = svc.transactions.query_by_batch(batch["id"], include_deleted=False)
    assert len(live) == 2


def test_rollback_preview_no_warning_for_recent_batch(isolated_stores):
    """Preview on a batch created < 24h ago must NOT include a 'warning'."""
    batch = _create_batch(age_hours=2, count=2)
    resp, _ = call("rollback_import_batch", batch_id=batch["id"])

    assert resp["ok"] is True
    assert "warning" not in resp


def test_rollback_deletes_and_returns_summary(isolated_stores):
    """A plain call deletes and returns batch summary + deleted count."""
    batch = _create_batch(age_hours=1, count=5)
    resp, _ = call("rollback_import_batch", batch_id=batch["id"])

    assert resp["ok"] is True
    assert "preview" not in resp
    assert resp["deleted"] == 5
    assert resp["batch"]["transaction_count"] == 5
    # All transactions are now soft-deleted
    live = svc.transactions.query_by_batch(batch["id"], include_deleted=False)
    assert len(live) == 0, "all transactions must be soft-deleted"
    # Batch status is rolled_back
    refreshed = svc.import_batches.find_by_id(batch["id"])
    assert refreshed["status"] == "rolled_back"


def test_rollback_without_preview_actually_deletes(isolated_stores):
    """`preview` is an opt-in DRY RUN, not a safety catch: omitting it deletes.

    It replaced a `confirm=true` the MODEL set to authorise its own call — the second
    confirm mechanism (ADR `2026-08-25-one-confirm-mechanism-for-every-tool`), removed here
    2026-08-27. Whether the owner is ASKED is the harness gate's decision, not a flag in the
    arguments, so the default had to become "do the thing" rather than "ask me again".
    """
    batch = _create_batch(age_hours=1, count=4)
    resp, _ = call("rollback_import_batch", batch_id=batch["id"])
    assert resp["ok"] is True
    assert "preview" not in resp


def test_rollback_already_rolled_back(isolated_stores):
    """Calling rollback on an already rolled-back batch returns an error."""
    batch = _create_batch(age_hours=1, count=2)
    # First rollback
    call("rollback_import_batch", batch_id=batch["id"])
    # Second rollback — should error
    resp, _ = call("rollback_import_batch", batch_id=batch["id"])
    assert resp["ok"] is False
    assert "already rolled back" in resp["error"].lower()


def test_rollback_batch_not_found(isolated_stores):
    resp, _ = call("rollback_import_batch", batch_id="nonexistent-id")
    assert resp["ok"] is False
    assert "not found" in resp["error"].lower()


# ── FIX B: restore_import_batch ──────────────────────────────────────────────


def test_rollback_restore_round_trip(isolated_stores):
    """rollback → restore restores all transactions and resets batch to 'active'."""
    batch = _create_batch(age_hours=1, count=3)
    batch_id = batch["id"]

    # Rollback
    rb, _ = call("rollback_import_batch", batch_id=batch_id, confirm=True)
    assert rb["ok"] is True
    assert rb["deleted"] == 3

    # Confirm all are deleted
    live_after_rb = svc.transactions.query_by_batch(batch_id, include_deleted=False)
    assert len(live_after_rb) == 0

    # Restore
    rs, _ = call("restore_import_batch", batch_id=batch_id)
    assert rs["ok"] is True
    assert rs["restored"] == 3

    # All transactions are live again
    live_after_rs = svc.transactions.query_by_batch(batch_id, include_deleted=False)
    assert len(live_after_rs) == 3

    # Batch status reset to active
    refreshed = svc.import_batches.find_by_id(batch_id)
    assert refreshed["status"] == "active"


def test_restore_refuses_active_batch(isolated_stores):
    """restore_import_batch on an active (not rolled-back) batch must return ok:False."""
    batch = _create_batch(age_hours=1, count=2)
    resp, _ = call("restore_import_batch", batch_id=batch["id"])

    assert resp["ok"] is False
    assert "rolled_back" in resp["error"]


def test_restore_batch_not_found(isolated_stores):
    resp, _ = call("restore_import_batch", batch_id="does-not-exist")
    assert resp["ok"] is False
    assert "not found" in resp["error"].lower()


# ── FIX C: verify_statement_balance mismatch diagnostics ─────────────────────


def _insert_verify_txns(account_id: str, rows: list[dict]):
    """Insert a list of raw transaction dicts (minimal schema) into the transaction store."""
    for row in rows:
        row.setdefault("id", str(uuid.uuid4()))
        row.setdefault("kind", "expense")
        row.setdefault("deleted", False)
        row.setdefault("tags", [])
        row.setdefault("splits", None)
        row.setdefault("cleared", False)
        row.setdefault("source", "ai")
        row.setdefault("external_refs", [])
        row.setdefault("merged_from", [])
        row.setdefault("merged_into", None)
        row.setdefault("note", "")
        row.setdefault("payee_id", None)
        row.setdefault("ticker", None)
        row.setdefault("quantity", None)
        row.setdefault("created_at", "2026-05-01T00:00:00Z")
        row.setdefault("category", "food")
        row.setdefault("description", "test")
        svc.transactions.insert(row)


def test_verify_mismatch_includes_largest_legs(isolated_stores, monkeypatch):
    """On mismatch, verify_statement_balance must include largest_legs (top-3 by amount)."""
    batch_id = str(uuid.uuid4())
    _insert_verify_txns(
        "acc-vnd",
        [
            {
                "from_account_id": "acc-vnd",
                "from_amount": 500_000.0,
                "from_currency": "VND",
                "to_account_id": None,
                "to_amount": None,
                "to_currency": None,
                "date": "2026-05-01",
                "batch_id": batch_id,
            },
            {
                "from_account_id": "acc-vnd",
                "from_amount": 300_000.0,
                "from_currency": "VND",
                "to_account_id": None,
                "to_amount": None,
                "to_currency": None,
                "date": "2026-05-02",
                "batch_id": batch_id,
            },
            {
                "from_account_id": "acc-vnd",
                "from_amount": 100_000.0,
                "from_currency": "VND",
                "to_account_id": None,
                "to_amount": None,
                "to_currency": None,
                "date": "2026-05-03",
                "batch_id": batch_id,
            },
            # Fourth row to ensure we cap at 3
            {
                "from_account_id": "acc-vnd",
                "from_amount": 50_000.0,
                "from_currency": "VND",
                "to_account_id": None,
                "to_amount": None,
                "to_currency": None,
                "date": "2026-05-04",
                "batch_id": batch_id,
            },
        ],
    )
    # opening_balance=1_000_000, closing_balance=1_050_000 → expected_net=50_000
    # actual_net = 0 - 950_000 = -950_000 → mismatch
    resp, _ = call(
        "verify_statement_balance",
        account_id="acc-vnd",
        opening_balance=1_000_000,
        closing_balance=1_050_000,
        batch_id=batch_id,
    )
    assert resp["status"] == "mismatch"
    assert "largest_legs" in resp
    legs = resp["largest_legs"]
    assert len(legs) == 3, "must return exactly top-3"
    # Sorted by descending amount
    assert legs[0]["amount"] == pytest.approx(500_000.0)
    assert legs[1]["amount"] == pytest.approx(300_000.0)
    assert legs[2]["amount"] == pytest.approx(100_000.0)
    # Each leg has direction, date, description
    assert legs[0]["direction"] == "OUT"
    assert legs[0]["date"] == "2026-05-01"


def test_verify_mismatch_hint_when_out_of_scale(isolated_stores):
    """hint is included when largest leg amount > 50 × |expected_net|."""
    batch_id = str(uuid.uuid4())
    # Real incident scale: expected_net=-790_125, leg=4_081_632_653
    # Here: expected_net=100, largest leg=100_000 (1000×)
    _insert_verify_txns(
        "acc-vnd",
        [
            {
                "from_account_id": "acc-vnd",
                "from_amount": 100_000.0,
                "from_currency": "VND",
                "to_account_id": None,
                "to_amount": None,
                "to_currency": None,
                "date": "2026-05-01",
                "batch_id": batch_id,
            },
        ],
    )
    resp, _ = call(
        "verify_statement_balance",
        account_id="acc-vnd",
        opening_balance=0,
        closing_balance=100,
        batch_id=batch_id,
    )
    assert resp["status"] == "mismatch"
    assert "hint" in resp
    assert "currency" in resp["hint"].lower()


def test_verify_mismatch_no_hint_when_sane(isolated_stores):
    """hint is NOT added when the largest leg is within normal scale of expected_net."""
    batch_id = str(uuid.uuid4())
    # expected_net = -1_000_000; leg = 900_000 → ratio = 0.9 (< 50)
    _insert_verify_txns(
        "acc-vnd",
        [
            {
                "from_account_id": "acc-vnd",
                "from_amount": 900_000.0,
                "from_currency": "VND",
                "to_account_id": None,
                "to_amount": None,
                "to_currency": None,
                "date": "2026-05-01",
                "batch_id": batch_id,
            },
        ],
    )
    resp, _ = call(
        "verify_statement_balance",
        account_id="acc-vnd",
        opening_balance=2_000_000,
        closing_balance=1_000_000,
        batch_id=batch_id,
    )
    assert resp["status"] == "mismatch"
    assert "hint" not in resp


def test_verify_account_currency_always_present(isolated_stores):
    """account_currency must be in the result whether matched or not."""
    batch_id = str(uuid.uuid4())
    _insert_verify_txns(
        "acc-vnd",
        [
            {
                "from_account_id": "acc-vnd",
                "from_amount": 100_000.0,
                "from_currency": "VND",
                "to_account_id": None,
                "to_amount": None,
                "to_currency": None,
                "date": "2026-05-01",
                "batch_id": batch_id,
            },
        ],
    )
    # Matching case: opening=200_000, closing=100_000 → expected_net=-100_000
    # actual = -100_000 → match
    resp, _ = call(
        "verify_statement_balance",
        account_id="acc-vnd",
        opening_balance=200_000,
        closing_balance=100_000,
        batch_id=batch_id,
    )
    assert "account_currency" in resp
    assert resp["account_currency"] == "VND"


def test_verify_ok_has_no_largest_legs(isolated_stores):
    """On a matching result, largest_legs must NOT be present."""
    batch_id = str(uuid.uuid4())
    _insert_verify_txns(
        "acc-vnd",
        [
            {
                "from_account_id": "acc-vnd",
                "from_amount": 100_000.0,
                "from_currency": "VND",
                "to_account_id": None,
                "to_amount": None,
                "to_currency": None,
                "date": "2026-05-01",
                "batch_id": batch_id,
            },
        ],
    )
    resp, _ = call(
        "verify_statement_balance",
        account_id="acc-vnd",
        opening_balance=200_000,
        closing_balance=100_000,
        batch_id=batch_id,
    )
    assert resp["status"] == "ok"
    assert "largest_legs" not in resp
    assert "hint" not in resp


def test_verify_hint_absent_when_expected_net_zero(isolated_stores):
    """When expected_net == 0 the hint must not appear (avoid divide-by-zero / false positive)."""
    batch_id = str(uuid.uuid4())
    _insert_verify_txns(
        "acc-vnd",
        [
            {
                "from_account_id": "acc-vnd",
                "from_amount": 999_000.0,
                "from_currency": "VND",
                "to_account_id": None,
                "to_amount": None,
                "to_currency": None,
                "date": "2026-05-01",
                "batch_id": batch_id,
            },
        ],
    )
    # opening == closing → expected_net == 0
    resp, _ = call(
        "verify_statement_balance",
        account_id="acc-vnd",
        opening_balance=500_000,
        closing_balance=500_000,
        batch_id=batch_id,
    )
    assert resp["status"] == "mismatch"
    # hint must NOT appear when expected_net == 0 (the threshold condition is guarded by != 0)
    assert "hint" not in resp


# ── FIX D: restore/rollforward must not resurrect user-explicitly-deleted txns ──


def test_restore_does_not_resurrect_pre_rollback_user_deletion(isolated_stores):
    """Transactions deleted by the user BEFORE rollback must NOT be restored.

    Regression for: restore_import_batch used query_by_batch(include_deleted=True)
    + filter on deleted=True, which cannot distinguish rollback-deletes from
    user-deletes. Fix: rollback records the exact ids it deleted; restore uses that list.
    """
    batch = _create_batch(age_hours=1, count=3)
    batch_id = batch["id"]

    # Simulate user explicitly deleting one transaction before any rollback
    all_txns = svc.transactions.query_by_batch(batch_id, include_deleted=False)
    user_deleted_id = all_txns[0]["id"]
    svc.transactions.bulk_soft_delete([user_deleted_id])

    # 2 live transactions remain
    assert len(svc.transactions.query_by_batch(batch_id, include_deleted=False)) == 2

    # Rollback (deletes the remaining 2)
    rb, _ = call("rollback_import_batch", batch_id=batch_id, confirm=True)
    assert rb["ok"] is True
    assert rb["deleted"] == 2  # only the 2 live ones were rolled back

    # Restore must NOT resurrect the user-deleted transaction
    rs, _ = call("restore_import_batch", batch_id=batch_id)
    assert rs["ok"] is True
    assert rs["restored"] == 2  # only the 2 rollback-deleted ones

    live = svc.transactions.query_by_batch(batch_id, include_deleted=False)
    live_ids = {t["id"] for t in live}
    assert user_deleted_id not in live_ids, "user-deleted txn must stay deleted after restore"
    assert len(live) == 2


def test_restore_does_not_resurrect_post_rollback_user_deletion(isolated_stores):
    """Transactions the user deletes AFTER rollback must not be resurrected by restore.

    After rollback all 3 txns are soft-deleted. User then explicitly deletes one
    (which is a no-op on the flag but the intent is clear). Restore must only
    un-delete the ids the rollback actually deleted, not rely on the current deleted flag.
    """
    batch = _create_batch(age_hours=1, count=3)
    batch_id = batch["id"]

    # Rollback all 3
    rb, _ = call("rollback_import_batch", batch_id=batch_id, confirm=True)
    assert rb["ok"] is True
    assert rb["deleted"] == 3

    # Verify rolled_back_txn_ids is stored on the batch
    refreshed = svc.import_batches.find_by_id(batch_id)
    assert "rolled_back_txn_ids" in refreshed
    assert len(refreshed["rolled_back_txn_ids"]) == 3

    # Restore un-deletes exactly the 3 rollback-deleted ids
    rs, _ = call("restore_import_batch", batch_id=batch_id)
    assert rs["ok"] is True
    assert rs["restored"] == 3

    live = svc.transactions.query_by_batch(batch_id, include_deleted=False)
    assert len(live) == 3


def test_rollforward_does_not_resurrect_pre_rollback_user_deletion(isolated_stores):
    """rollforward_import_batch must have the same fix: only restore the rollback list."""
    batch = _create_batch(age_hours=1, count=4)
    batch_id = batch["id"]

    # User deletes one txn before any rollback
    all_txns = svc.transactions.query_by_batch(batch_id, include_deleted=False)
    user_deleted_id = all_txns[0]["id"]
    svc.transactions.bulk_soft_delete([user_deleted_id])

    # Rollback remaining 3
    rb, _ = call("rollback_import_batch", batch_id=batch_id, confirm=True)
    assert rb["ok"] is True
    assert rb["deleted"] == 3

    # rollforward must restore only the 3 rollback-deleted ids
    rf, _ = call("restore_import_batch", batch_id=batch_id)
    assert rf["ok"] is True
    assert rf["restored"] == 3

    live = svc.transactions.query_by_batch(batch_id, include_deleted=False)
    live_ids = {t["id"] for t in live}
    assert user_deleted_id not in live_ids, "user-deleted txn must stay deleted after rollforward"
    assert len(live) == 3


def test_rollback_records_exact_txn_ids_on_batch(isolated_stores):
    """rollback_import_batch must persist rolled_back_txn_ids on the batch record."""
    batch = _create_batch(age_hours=1, count=3)
    batch_id = batch["id"]

    all_txns = svc.transactions.query_by_batch(batch_id, include_deleted=False)
    expected_ids = {t["id"] for t in all_txns}

    call("rollback_import_batch", batch_id=batch_id, confirm=True)

    refreshed = svc.import_batches.find_by_id(batch_id)
    assert "rolled_back_txn_ids" in refreshed
    assert set(refreshed["rolled_back_txn_ids"]) == expected_ids
