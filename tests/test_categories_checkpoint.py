"""
Checkpoint-capture test for a category MERGE (money-data safety).

Under the opaque-id contract (docs/adr/2026-07-05-category-opaque-ids.md), a plain relabel
never touches transactions/profile — the id never changes, so there's nothing to re-key.
Only a MERGE (``rename_category(key, merge_into=...)``) folds one category's ledger/budget
data into another and deletes the loser's row, so the merge path is what needs the ledger and
profile pre-write snapshots. The test verifies that a merge captures those snapshots, and that
the categories.json store write is also checkpoint-covered.

Fully isolated: checkpoint + store + profile paths monkeypatched into tmp_path.
"""

from __future__ import annotations

import pytest

from backend.ai import checkpoint as ckpt
from backend.core import io
from backend.db.category_store import CategoryStore
from backend.db.profile_store import ProfileStore
from backend.models.category_defs import BUILTIN_CATEGORIES
from backend.services import categories as cat


@pytest.fixture
def isolated(tmp_path, monkeypatch, db_isolated):
    """transaction_store is DB-backed now (plan 3 task 4) — db_isolated redirects it (and
    every other unpinned store) to a fresh, empty database; no more per-instance _path to
    repoint, and no file to pre-seed "[]" into (an isolated DB starts empty)."""
    data_dir = tmp_path / "data"
    blobs = tmp_path / "checkpoints" / "blobs"
    index = tmp_path / "checkpoints" / "index.json"
    data_dir.mkdir(parents=True, exist_ok=True)
    blobs.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(ckpt, "DATA_DIR", data_dir)
    monkeypatch.setattr(ckpt, "CHECKPOINTS_BLOBS", blobs)
    monkeypatch.setattr(ckpt, "CHECKPOINTS_INDEX", index)

    # profile is DB-backed now (Wave 5, JSON->DB) — db_isolated already gives it a fresh,
    # empty row; seed it directly through ProfileStore.
    ProfileStore().set_raw({"base_currency": "SGD"})

    # Seed the store (includes all built-ins + split).
    CategoryStore().bulk_insert(BUILTIN_CATEGORIES)

    prev = io._write_hook
    ckpt.register_write_hook(ckpt._snapshot_active)
    yield tmp_path
    io.register_write_hook(prev)


def test_merge_snapshots_ledger_and_profile(isolated):
    from tests.conftest import seed_stub_accounts

    key = cat.create_category("Startup", "expense")["id"]
    venture_id = cat.create_category("Venture", "expense")["id"]
    # transaction_store is DB-backed now (plan 3 task 4) — seed via replace_all (wipes +
    # reinserts, same "this IS the whole ledger" semantics as the old transactions.json
    # full-file overwrite), not a raw file write.
    txn = {"id": "t1", "kind": "expense", "date": "2026-06-01", "category": key}
    seed_stub_accounts([txn])
    cat.transaction_store.replace_all([txn])
    # Seed a budget entry so the profile gets written during the merge re-key. profile is
    # DB-backed now (Wave 5, JSON->DB) — seed directly through ProfileStore.
    ProfileStore().set_raw(
        {
            "base_currency": "SGD",
            "monthly_budgets": {key: {"limit": 100.0, "rollover": False}},
        }
    )

    ctx, token = ckpt.begin_turn("sess-cat", anchor_history_len=0, message_preview="merge")
    try:
        cat.rename_category(key, merge_into=venture_id)
    finally:
        ckpt.end_turn(ctx, token)

    # The ledger + categories + profile (all DB-backed, spec D7) were ALL captured → the
    # merge is undoable as one unit. profile.json's file-level capture (ctx.files) is gone
    # now that ProfileStore fires the DB write hook instead — _db_tables is the DB mirror.
    assert "transactions" in ctx._db_tables
    assert "profile" in ctx._db_tables
    # The categories store write (deleting the loser's row) is also checkpoint-covered
    # (plan 4a task 2 — categories is DB-backed now, no longer categories.json).
    assert "categories" in ctx._db_tables

    # The snapshot is the PRE-write ledger (still on the old id) — DB-backed stores record
    # their before-image as a write_log row (checkpoint.py's CheckpointContext.record_db_before,
    # spec D7) rather than a file blob.
    from sqlalchemy import select

    from backend.db import tables as db_tables
    from backend.db.engine import get_engine

    with get_engine().connect() as conn:
        row = conn.execute(
            select(db_tables.write_log.c.before_payload).where(
                db_tables.write_log.c.checkpoint_id == ctx.id,
                db_tables.write_log.c.table_name == "transactions",
                db_tables.write_log.c.pk == "t1",
            )
        ).scalar_one()
    assert row["category"] == key


def test_profile_not_excluded_from_checkpoints(isolated):
    # Guards the design intent: profile re-keys (money config) must be undoable. profile is
    # DB-backed now (Wave 5, JSON->DB) — _EXCLUDED_TABLES is the DB mirror of _EXCLUDED_PATHS.
    assert "profile" not in ckpt._EXCLUDED_TABLES
