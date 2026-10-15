"""MergeJournalStore — DB-backed (plan 3 task 4). PK is absorbed_id (not the generic
id), hand-rolled — full public-surface parity: record/pop, plus the inherited-JsonStore
CRUD surface. This store is NOT checkpoint-excluded (its file, merge_journal.json, was
never in ai.checkpoint._EXCLUDED_PATHS — a file-level-truth finding this task's report
documents) — proven here directly.
"""

from __future__ import annotations

from backend.db.merge_journal_store import MergeJournalStore


def _entry(absorbed_id, canonical_id="txn_c", **kw):
    return {
        "absorbed_id": absorbed_id,
        "canonical_id": canonical_id,
        "canonical_before": {"id": canonical_id},
        "absorbed_before": {"id": absorbed_id},
        "become_transfer": False,
        "reason": "dup",
        "created_at": "2026-01-01T00:00:00Z",
        **kw,
    }


def test_record_then_find_by_id(db_isolated):
    store = MergeJournalStore()
    entry = store.record(_entry("txn_a"))
    assert entry["absorbed_id"] == "txn_a"
    assert store.find_by_id("txn_a") == entry


def test_record_replaces_existing_in_place(db_isolated):
    from sqlalchemy import select

    from backend.db.engine import get_engine
    from backend.db.tables import merge_journal as merge_journal_table

    store = MergeJournalStore()
    store.record(_entry("txn_a", reason="first"))
    with get_engine().connect() as conn:
        seq_before = conn.execute(
            select(merge_journal_table.c.seq).where(merge_journal_table.c.absorbed_id == "txn_a")
        ).scalar_one()
    store.record(_entry("txn_a", reason="second"))
    assert store.find_by_id("txn_a")["reason"] == "second"
    with get_engine().connect() as conn:
        seq_after = conn.execute(
            select(merge_journal_table.c.seq).where(merge_journal_table.c.absorbed_id == "txn_a")
        ).scalar_one()
    assert seq_after == seq_before


def test_pop_returns_and_removes(db_isolated):
    store = MergeJournalStore()
    store.record(_entry("txn_a"))
    popped = store.pop("txn_a")
    assert popped["absorbed_id"] == "txn_a"
    assert store.find_by_id("txn_a") is None


def test_pop_missing_returns_none(db_isolated):
    assert MergeJournalStore().pop("nope") is None


def test_all_and_query_and_count(db_isolated):
    store = MergeJournalStore()
    store.record(_entry("txn_a", canonical_id="txn_c1"))
    store.record(_entry("txn_b", canonical_id="txn_c2"))
    assert {e["absorbed_id"] for e in store.all()} == {"txn_a", "txn_b"}
    assert [e["absorbed_id"] for e in store.query(canonical_id="txn_c1")] == ["txn_a"]
    assert store.count() == 2


def test_generic_crud_surface(db_isolated):
    store = MergeJournalStore()
    store.insert(_entry("txn_a"))
    merged = store.update_by_id("txn_a", {"reason": "changed"})
    assert merged["reason"] == "changed"
    assert store.delete("txn_a") is True
    assert store.find_by_id("txn_a") is None


def test_bulk_insert_and_bulk_patch(db_isolated):
    store = MergeJournalStore()
    store.bulk_insert([_entry("txn_a"), _entry("txn_b")])
    assert store.count() == 2
    patched = store.bulk_patch({"txn_a": {"reason": "x"}, "missing": {"reason": "y"}})
    assert set(patched) == {"txn_a"}


def test_replace_all(db_isolated):
    store = MergeJournalStore()
    store.record(_entry("old"))
    store.replace_all([_entry("txn_a")])
    assert store.find_by_id("old") is None
    assert [e["absorbed_id"] for e in store.all()] == ["txn_a"]


def test_write_fires_checkpoint_hook_and_is_restorable(db_isolated):
    """Finding (this task's report): merge_journal.json was never checkpoint-excluded
    at the file level, so the DB table stays checkpoint-INCLUDED too — a restore must
    revert a recorded merge-journal entry, not leave it as a dangling undo-snapshot."""
    from backend.ai import checkpoint as cp

    store = MergeJournalStore()
    ctx, token = cp.begin_turn("s1", 0, "t")
    try:
        store.record(_entry("txn_a"))
    finally:
        entry = cp.end_turn(ctx, token)
    assert entry is not None
    assert "merge_journal" in entry["db_tables"]
    cp.restore(entry["id"], mode="data")
    assert store.find_by_id("txn_a") is None


def test_conn_composition_atomic_with_caller(db_isolated):
    from backend.db.engine import locked_unit_of_work
    from backend.db.tables import merge_journal as merge_journal_table

    store = MergeJournalStore()
    with locked_unit_of_work(merge_journal_table) as conn:
        store.record(_entry("txn_a"), conn=conn)
    assert store.find_by_id("txn_a") is not None
