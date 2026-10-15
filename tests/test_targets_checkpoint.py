"""
Checkpoint-capture test for TargetsStore (P2-BU1 — AC-14).

TargetsStore is DB-backed (plan 4a task 3) — a write fires core.io.fire_db_write_hook, so
within an active turn the pre-write row image is captured (write_log row-level checkpoints,
spec D7) and a restore would revert it — the "targets" table is intentionally NOT in
ai.checkpoint._EXCLUDED_TABLES. Mirrors tests/test_category_color.py's own
test_color_is_checkpoint_covered.

Fully isolated: db_isolated gives this test its own fresh, migrated database (no live data).
"""

from __future__ import annotations

import pytest

from backend.ai import checkpoint as ckpt
from backend.db.targets_store import TargetsStore


@pytest.fixture
def isolated(db_isolated):
    return db_isolated


def test_target_write_is_snapshotted(isolated):
    store = TargetsStore()
    store.create({"name": "EF", "target_amount": 1000, "target_date": "2026-12-31"})  # 1 row

    ctx, token = ckpt.begin_turn("sess-bu1", anchor_history_len=0, message_preview="t")
    try:
        store.create({"name": "Trip", "target_amount": 500, "target_date": "2026-09-30"})
    finally:
        entry = ckpt.end_turn(ctx, token)

    assert entry is not None
    assert "targets" in entry["db_tables"]


def test_targets_not_excluded_from_checkpoints(isolated):
    # Guards the design intent: target edits must be undoable.
    assert "targets" not in ckpt._EXCLUDED_TABLES
