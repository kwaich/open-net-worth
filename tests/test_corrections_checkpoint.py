"""
Checkpoint-EXCLUSION test for corrections.json (P2-SP2).

A correction event is a runtime/behavioral signal, NOT user-authored data, so a Lucius
restore must NOT resurrect a stale correction history. corrections.json is therefore in
checkpoint._EXCLUDED_PATHS — a write to it within an active turn is NOT snapshotted. This
mirrors the existing exclusion treatment of cost_log / signals (and is the inverse of
test_targets_checkpoint.py, where targets.json is intentionally captured).

Fully isolated: checkpoint + store paths monkeypatched into tmp_path (no live data).
"""

from __future__ import annotations

import pytest

from backend.ai import checkpoint as ckpt
from backend.core import io
from backend.db.correction_store import CorrectionStore


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    blobs = tmp_path / "checkpoints" / "blobs"
    index = tmp_path / "checkpoints" / "index.json"
    data_dir.mkdir(parents=True, exist_ok=True)
    blobs.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(ckpt, "DATA_DIR", data_dir)
    monkeypatch.setattr(ckpt, "CHECKPOINTS_BLOBS", blobs)
    monkeypatch.setattr(ckpt, "CHECKPOINTS_INDEX", index)
    # The exclusion set is resolved at import; rebuild it so the temp path is recognized.
    monkeypatch.setattr(
        ckpt, "_EXCLUDED_PATHS", ckpt._EXCLUDED_PATHS | {(data_dir / "corrections.json").resolve()}
    )

    prev = io._write_hook
    ckpt.register_write_hook(ckpt._snapshot_active)
    yield tmp_path
    io.register_write_hook(prev)


def test_correction_write_is_not_snapshotted(isolated):
    store = CorrectionStore()
    store.record({"payee_key": "starbucks", "to_category": "food", "kind": "expense"})

    ctx, token = ckpt.begin_turn("sess-sp2", anchor_history_len=0, message_preview="c")
    try:
        store.record({"payee_key": "grab", "to_category": "transport", "kind": "expense"})
    finally:
        ckpt.end_turn(ctx, token)

    # corrections.json was NOT captured (excluded) — a restore won't resurrect the log.
    assert "corrections.json" not in ctx.files


def test_corrections_in_excluded_set():
    # Guards the design intent against the real module-level path (not the temp one).
    from backend.paths import CORRECTIONS_JSON

    assert CORRECTIONS_JSON.resolve() in ckpt._EXCLUDED_PATHS
