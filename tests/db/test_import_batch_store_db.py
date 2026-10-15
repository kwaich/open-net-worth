"""ImportBatchStore — DB-backed (plan 3 task 4). Full public-surface parity:
create_batch (incl. its conditional-key-inclusion for optional args), set_balance_check,
set_rolled_back_txn_ids, query_by_session, set_status, list_batches (sorted, newest
first), plus the inherited-JsonStore CRUD surface.
"""

from __future__ import annotations

from backend.db.import_batch_store import ImportBatchStore


def test_create_batch_defaults(db_isolated):
    store = ImportBatchStore()
    batch = store.create_batch("CSV import", 3)
    assert batch["id"].startswith("imp_")
    assert batch["description"] == "CSV import"
    assert batch["transaction_count"] == 3
    assert batch["status"] == "active"
    assert batch["source_account_id"] is None
    # Optional keys never SET are genuinely ABSENT from the stored payload, not None.
    assert "import_session_id" not in batch
    assert "statement_ref_id" not in batch
    assert "balance_check" not in batch
    stored = store.find_by_id(batch["id"])
    assert "import_session_id" not in stored
    assert "balance_check" not in stored


def test_create_batch_conditional_keys_present_when_given(db_isolated):
    store = ImportBatchStore()
    batch = store.create_batch(
        "Statement import",
        5,
        source_account_id="acc_1",
        balance_check={"ok": True},
        import_session_id="sess_1",
        statement_ref_id="stmt_1",
    )
    assert batch["import_session_id"] == "sess_1"
    assert batch["statement_ref_id"] == "stmt_1"
    assert batch["balance_check"] == {"ok": True}
    assert batch["source_account_id"] == "acc_1"


def test_set_balance_check(db_isolated):
    store = ImportBatchStore()
    batch = store.create_batch("d", 1)
    updated = store.set_balance_check(batch["id"], {"ok": False, "delta": 1.5})
    assert updated["balance_check"] == {"ok": False, "delta": 1.5}


def test_set_rolled_back_txn_ids(db_isolated):
    store = ImportBatchStore()
    batch = store.create_batch("d", 1)
    updated = store.set_rolled_back_txn_ids(batch["id"], ["t1", "t2"])
    assert updated["rolled_back_txn_ids"] == ["t1", "t2"]


def test_set_status(db_isolated):
    store = ImportBatchStore()
    batch = store.create_batch("d", 1)
    updated = store.set_status(batch["id"], "rolled_back")
    assert updated["status"] == "rolled_back"
    assert store.find_by_id(batch["id"])["status"] == "rolled_back"


def test_query_by_session(db_isolated):
    store = ImportBatchStore()
    store.create_batch("a", 1, import_session_id="s1")
    store.create_batch("b", 1, import_session_id="s1")
    store.create_batch("c", 1, import_session_id="s2")
    ids = {b["id"] for b in store.query_by_session("s1")}
    assert len(ids) == 2
    assert store.query_by_session("s-none") == []


def test_list_batches_sorted_newest_first_and_status_filter(db_isolated):
    store = ImportBatchStore()
    b1 = store.create_batch("first", 1)
    b2 = store.create_batch("second", 1)
    # Both batches can mint the SAME second-resolution created_at when created back to
    # back — force a real, unambiguous ordering rather than relying on wall-clock drift.
    store.update_by_id(b1["id"], {"created_at": "2026-01-01T00:00:00Z"})
    store.update_by_id(b2["id"], {"created_at": "2026-01-01T00:00:01Z"})
    store.set_status(b2["id"], "rolled_back")
    listed = store.list_batches()
    assert [b["id"] for b in listed] == [b2["id"], b1["id"]]  # created_at descending
    assert [b["id"] for b in store.list_batches(status="rolled_back")] == [b2["id"]]
    assert [b["id"] for b in store.list_batches(status="active")] == [b1["id"]]


def test_generic_crud_surface(db_isolated):
    store = ImportBatchStore()
    batch = store.create_batch("d", 1)
    assert store.count() == 1
    store.delete(batch["id"])
    assert store.find_by_id(batch["id"]) is None


def test_bulk_patch_and_replace_all(db_isolated):
    store = ImportBatchStore()
    b1 = store.create_batch("a", 1)
    b2 = store.create_batch("b", 1)
    patched = store.bulk_patch({b1["id"]: {"status": "rolled_back"}})
    assert patched[b1["id"]]["status"] == "rolled_back"
    store.replace_all([{**b2, "description": "replaced"}])
    assert store.find_by_id(b1["id"]) is None
    assert store.find_by_id(b2["id"])["description"] == "replaced"


def test_write_fires_checkpoint_hook(db_isolated):
    from backend.ai import checkpoint as cp

    store = ImportBatchStore()
    ctx, token = cp.begin_turn("s1", 0, "t")
    try:
        store.create_batch("d", 1)
    finally:
        entry = cp.end_turn(ctx, token)
    assert entry is not None
    assert "import_batches" in entry["db_tables"]
