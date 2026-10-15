"""Characterization tests for the inverted checkpoint write-hook (RFC P1-A4).

The checkpoint→storage dependency is inverted: db/ no longer imports ai/. Instead
ai.checkpoint registers a snapshot callback into the core.io write-hook seam at
import, and each file-backed store (skill_store, budgets' profile.json, portability's
session bundles — formerly JsonStore._save_raw too, before its retirement) fires
that hook (core.io.fire_write_hook) before each write. These tests lock in the
*behavior* that must stay unchanged:

  1. Within an active begin_turn, a file write snapshots the pre-write file
     into the checkpoint (ctx.files[name] set, blob written).
  2. PriceHistoryStore writes do NOT create a snapshot (excluded by design — it's
     append-only cache data, not per-turn AI state).
  3. importing ai.checkpoint registers the pre-write hook on core.io (the seam),
     proving db/ observes checkpoints by injection, not by importing ai/.

Fully isolated: every store + checkpoint path is monkeypatched into a tmp_path,
so these never read or write live data (project rule: no destructive tests on
live data).
"""

from __future__ import annotations

import json

import pytest

from backend.ai import checkpoint as ckpt
from backend.core import io
from backend.db.price_history_store import PriceHistoryStore


@pytest.fixture
def isolated(tmp_path, monkeypatch, db_isolated):
    """Point all checkpoint + store paths at a tmp dir and ensure the snapshot
    hook is (re)registered for the test, restoring the live hook afterwards.
    PriceHistoryStore resolves against the per-test isolated database (db_isolated)."""
    data_dir = tmp_path / "data"
    blobs = tmp_path / "checkpoints" / "blobs"
    index = tmp_path / "checkpoints" / "index.json"
    data_dir.mkdir(parents=True, exist_ok=True)
    blobs.mkdir(parents=True, exist_ok=True)

    # ai.checkpoint reads these module-level constants (imported from backend.paths).
    monkeypatch.setattr(ckpt, "DATA_DIR", data_dir)
    monkeypatch.setattr(ckpt, "CHECKPOINTS_BLOBS", blobs)
    monkeypatch.setattr(ckpt, "CHECKPOINTS_INDEX", index)
    # Recompute the excluded-paths set is unnecessary; we use non-excluded files.

    # Re-install the snapshot hook for the duration of the test, restore after.
    prev = io._write_hook
    ckpt.register_write_hook(ckpt._snapshot_active)
    yield tmp_path
    io.register_write_hook(prev)


def test_import_registers_pre_write_hook():
    """Importing ai.checkpoint installs its snapshot callback on the core.io seam —
    this is the inversion: db sees checkpoints via the registry, not via importing ai."""
    assert io._write_hook is ckpt._snapshot_active


def _write_rows(path, rows) -> None:
    """The fire_write_hook-then-atomic_write_text sequence every real file-store
    write uses (skill_store, budgets, portability session bundles; formerly
    JsonStore._save_raw too) — the hook fires on the PRE-write content, then the
    write lands."""
    path.parent.mkdir(parents=True, exist_ok=True)
    io.fire_write_hook(path)
    io.atomic_write_text(path, json.dumps(rows))


def test_file_write_snapshots_into_active_turn(isolated):
    """A file write inside an active turn captures the pre-write file into the
    checkpoint (manifest entry + blob), via the registered hook — not a direct import."""
    path = isolated / "data" / "things.json"
    _write_rows(path, [{"id": "a", "v": 1}])  # file now exists with one row

    ctx, token = ckpt.begin_turn("sess-1", anchor_history_len=0, message_preview="t")
    try:
        _write_rows(path, [{"id": "a", "v": 1}, {"id": "b", "v": 2}])  # pre-write snapshot
    finally:
        ckpt.end_turn(ctx, token)

    # The pre-write content was snapshotted under the file's name.
    assert "things.json" in ctx.files
    blob_ref = ctx.files["things.json"]
    assert blob_ref is not None and blob_ref.startswith("sha256:")
    # The captured blob reflects the PRE-write state (one row), not the post-write (two).
    snapped = json.loads(ckpt._blob_bytes(blob_ref))
    assert [r["id"] for r in snapped] == ["a"]


def test_file_write_outside_turn_does_not_snapshot(isolated):
    """No active turn → the hook is a no-op and the write still lands."""
    path = isolated / "data" / "things2.json"
    assert ckpt.get_active() is None
    _write_rows(path, [{"id": "x"}])  # must not raise; fires hook (no-op) then writes
    assert path.exists()


def test_price_history_write_is_not_snapshotted(isolated):
    """PriceHistoryStore is excluded from checkpoints by design (append-only cache):
    it writes via core.io.atomic_write_text directly and never fires the hook."""
    store = PriceHistoryStore()
    ctx, token = ckpt.begin_turn("sess-2", anchor_history_len=0, message_preview="t")
    try:
        store.record("AAPL", 123.45, day="2026-06-04", source="yahoo")
    finally:
        ckpt.end_turn(ctx, token)

    assert ctx.files == {}  # nothing snapshotted
    assert not ctx.has_writes


# ── accounts.json + transactions.json companion pairing — RETIRED (plan 3 task 4) ──
#
# The three tests that lived here proved `_COMPANIONS`'s file-pairing behavior: a turn that
# wrote only transactions.json also captured (and therefore restored) accounts.json, so an
# account's `opening_txn_id` pointer could never survive a restore dangling. `accounts`/
# `transactions` are DB-backed as of this task, `_COMPANIONS` is now permanently empty (see
# ai/checkpoint.py's own comment — row-level write_log images already capture every touched
# row in every table a turn touches, making a table-name companions map structurally
# redundant for them), and neither store writes accounts.json/transactions.json again — so
# these tests characterized a mechanism that no longer runs for these two files, not a
# regression. The SAME invariant, proven against the NEW mechanism, now lives in
# tests/db/test_write_log_checkpoints.py::test_a_transactions_write_also_captures_the_
# opening_pointer_row_no_companions_map_needed.


# ── BUG-HUNT REGRESSION: preview() orphan-exclusion ─────────────────────────


def test_preview_excludes_orphaned_cross_session_changes(isolated):
    """Checkpoints whose status is NOT "active" (e.g. "orphaned" after a prior restore)
    must NOT appear in the cross_session_changes count from preview().

    Regression: the filter previously only checked kind=="turn" but not status=="active",
    so already-undone checkpoints were overcounting the warning.
    """
    import json

    # A data file — needed for snapshot_before_write to have something to capture.
    data_file = isolated / "data" / "accounts.json"
    data_file.write_text(json.dumps([{"id": "a1"}]))

    # Session A: take a checkpoint (this is the one we'll later preview).
    ctx_a, tok_a = ckpt.begin_turn("sess-A", anchor_history_len=0, message_preview="turn A")
    ckpt._snapshot_active(data_file)
    entry_a = ckpt.end_turn(ctx_a, tok_a)
    assert entry_a is not None, "session A turn must have written something"
    cp_id_a = entry_a["id"]

    # Session B: a LATER turn from a DIFFERENT session (would normally appear as cross-session).
    ctx_b, tok_b = ckpt.begin_turn("sess-B", anchor_history_len=0, message_preview="turn B")
    ckpt._snapshot_active(data_file)
    entry_b = ckpt.end_turn(ctx_b, tok_b)
    assert entry_b is not None

    # Before orphaning: session B's active checkpoint appears in cross_session_changes.
    result_before = ckpt.preview(cp_id_a)
    assert len(result_before["cross_session_changes"]) == 1, (
        "active cross-session checkpoint must be counted before orphaning"
    )

    # Simulate a prior restore: mark session B's checkpoint as orphaned.
    index = ckpt._read_index()
    for e in index:
        if e.get("id") == entry_b["id"]:
            e["status"] = "orphaned"
    ckpt._write_index(index)

    # After orphaning: the already-undone checkpoint must NOT appear in the warning.
    result_after = ckpt.preview(cp_id_a)
    assert result_after["cross_session_changes"] == [], (
        "orphaned checkpoint must not appear in cross_session_changes (overcount regression)"
    )
