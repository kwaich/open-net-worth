"""Checkpoint-capture test for receipts (reversed 2026-08-21 — see the amendment on ADR
``2026-08-15-separate-attachments-receipts-and-statements.md``).

``receipts``/``transaction_receipts`` used to be checkpoint-EXCLUDED ("a Lucius restore
must never resurrect a user-deleted receipt or relink one explicitly unlinked"). That
reasoning didn't survive scrutiny: ``write_log`` checkpointing is ROW-LEVEL and
first-touch-per-turn (ADR ``2026-08-07-write-log-row-level-checkpoints``), so a restore
only ever replays the exact ``(table, pk)`` rows a SPECIFIC turn wrote — it structurally
cannot resurrect or relink a row a DIFFERENT, later write touched. Every other user-facing
table in this app (transactions, accounts, categories, every money-core/planning table)
already relies on exactly that guarantee. This file pins the new, opposite behavior:
attaching or unlinking a receipt inside a turn is now undoable via ``/rewind``, mirroring
``tests/test_planning_order_checkpoint.py``'s own capture-then-restore shape.

Fully isolated: ``db_isolated`` gives a fresh, migrated database; receipt blobs are
redirected under ``tmp_path``.
"""

from __future__ import annotations

import pytest

from backend.ai import checkpoint as ckpt
from backend.services import ledger as svc
from backend.services.receipts import apply_receipt_change, link_store, receipt_store


@pytest.fixture
def isolated(tmp_path, monkeypatch, db_isolated):
    import backend.db.receipt_store as rcpt_mod

    rcpt_dir = tmp_path / "receipts"
    rcpt_dir.mkdir()
    monkeypatch.setattr(rcpt_mod, "RECEIPTS_DIR", rcpt_dir)

    monkeypatch.setattr(svc, "base_currency", lambda: "SGD")
    svc.accounts.insert({"id": "acc_chk", "name": "Checking", "kind": "asset", "currency": "SGD"})
    txn = svc.create_transaction(
        {
            "kind": "expense",
            "date": "2026-08-01",
            "description": "Groceries",
            "category": None,
            "from_account_id": "acc_chk",
            "from_amount": 42.0,
            "from_currency": "SGD",
        }
    )
    return {"txn_id": txn["id"]}


def test_receipts_not_excluded_from_checkpoints():
    # Guards the design intent: receipt writes must be undoable, like every other choke.
    assert "receipts" not in ckpt._EXCLUDED_TABLES
    assert "transaction_receipts" not in ckpt._EXCLUDED_TABLES


def test_upload_receipt_is_checkpoint_covered_and_restore_reverts_it(isolated):
    ctx, token = ckpt.begin_turn("sess-rcpt", anchor_history_len=0, message_preview="attach")
    try:
        result = apply_receipt_change(
            {
                "type": "upload",
                "content": b"jpeg-bytes-here",
                "filename": "receipt.jpg",
                "media_type": "image/jpeg",
                "transaction_id": isolated["txn_id"],
            }
        )
    finally:
        entry = ckpt.end_turn(ctx, token)

    receipt_id = result["receipt"]["id"]
    link_id = result["link"]["id"]
    assert receipt_store.find_by_id(receipt_id) is not None
    assert link_store.find_by_id(link_id) is not None

    # Both writes landed in the SAME checkpoint (one turn, one unit) — not two.
    assert entry is not None
    assert "receipts" in entry["db_tables"]
    assert "transaction_receipts" in entry["db_tables"]

    ckpt.restore(entry["id"], mode="data")

    # Restore reverts BOTH rows: the receipt row didn't exist before this turn, and
    # neither did the link — a rewind must delete both, not resurrect a stale link to a
    # receipt that no longer exists.
    assert receipt_store.find_by_id(receipt_id) is None
    assert link_store.find_by_id(link_id) is None


def test_unlink_receipt_is_checkpoint_covered_and_restore_relinks_it(isolated):
    promoted = apply_receipt_change(
        {
            "type": "upload",
            "content": b"jpeg-bytes-here",
            "filename": "receipt.jpg",
            "media_type": "image/jpeg",
            "transaction_id": isolated["txn_id"],
        }
    )
    receipt_id = promoted["receipt"]["id"]
    link_id = promoted["link"]["id"]

    ctx, token = ckpt.begin_turn("sess-rcpt-2", anchor_history_len=0, message_preview="unlink")
    try:
        apply_receipt_change({"type": "unlink", "link_id": link_id})
    finally:
        entry = ckpt.end_turn(ctx, token)

    assert entry is not None
    assert "transaction_receipts" in entry["db_tables"]
    assert link_store.find_by_id(link_id) is None

    ckpt.restore(entry["id"], mode="data")

    # The receipt the user "explicitly unlinked" earlier in the turn is relinked by this
    # turn's own restore — exactly the case the old ADR text worried about, now correctly
    # scoped to only what THIS turn did (it never touched a link from any OTHER turn).
    restored_link = link_store.find_by_id(link_id)
    assert restored_link is not None
    assert restored_link["receipt_id"] == receipt_id
    # The receipt row itself was never touched by the unlink — untouched by restore too.
    assert receipt_store.find_by_id(receipt_id) is not None
