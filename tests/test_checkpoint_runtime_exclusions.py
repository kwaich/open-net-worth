"""Regression: runtime bookkeeping writes must NOT mint checkpoints.

2026-07-11 bug: the model-telemetry store writes on EVERY model turn via
JsonStore._save_raw → fire_write_hook → CheckpointContext.has_writes — so every Q&A
chat turn minted a checkpoint and the chat showed a Restore button on turns that
changed no data.
RUN_HISTORY_JSON had the same latent gap (its store doc claims checkpoint-EXCLUDED;
checkpoint.py only ever mentioned it in a comment).

INT6 code-review blocker (2026-07-16): PENDING_PUSHES_JSON (the push-delivery retry
queue, backend/monitoring/push_retry.py) was documented everywhere as checkpoint-
EXCLUDED but never actually added to _EXCLUDED_PATHS — the same bug class this file
exists to catch. Storage plan 4E task 1 moved that store to a DB table
(``pending_pushes``, ``checkpointed=False``), so its pin now lives at the TABLE level
(``_EXCLUDED_TABLES``) alongside ``artifacts``/``uploads`` — see
test_excluded_tables_contains_the_misc_tables below — not in _EXCLUDED_PATHS.

Pins: telemetry/run-history writes inside a turn → NO checkpoint; the migrated misc
tables (artifacts/uploads/pending_pushes) checkpoint-excluded at the table level; a
real user-data write (transactions.json) → checkpoint, unchanged.
"""

from __future__ import annotations

import pytest

from backend.ai import checkpoint as ckpt
from backend.core import io
from backend.paths import (
    MODEL_TELEMETRY_JSON,
    RUN_HISTORY_JSON,
    TRANSACTIONS_JSON,
)


@pytest.fixture
def turn(tmp_path, monkeypatch):
    blobs = tmp_path / "checkpoints" / "blobs"
    index = tmp_path / "checkpoints" / "index.json"
    blobs.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(ckpt, "CHECKPOINTS_BLOBS", blobs)
    monkeypatch.setattr(ckpt, "CHECKPOINTS_INDEX", index)

    ctx, token = ckpt.begin_turn("sess-x", 1, "test turn")
    yield ctx
    ckpt.end_turn(ctx, token)


def _finalise(ctx) -> "dict | None":
    return ctx.finalise()


@pytest.mark.parametrize(
    "path",
    [MODEL_TELEMETRY_JSON, RUN_HISTORY_JSON],
    ids=["model_telemetry", "run_history"],
)
def test_runtime_log_write_mints_no_checkpoint(turn, path):
    io.fire_write_hook(path)
    assert _finalise(turn) is None, f"{path.name} write must not create a checkpoint"


def test_user_data_write_still_checkpoints(turn, tmp_path):
    # A real store write must keep minting checkpoints exactly as before.
    TRANSACTIONS_JSON.parent.mkdir(parents=True, exist_ok=True)
    if not TRANSACTIONS_JSON.exists():
        pytest.skip("no transactions.json on this machine — covered by store tests")
    io.fire_write_hook(TRANSACTIONS_JSON)
    entry = _finalise(turn)
    assert entry is not None and entry["files"]


def test_excluded_paths_contains_the_runtime_logs():
    excluded = ckpt._EXCLUDED_PATHS
    for p in (MODEL_TELEMETRY_JSON, RUN_HISTORY_JSON):
        assert p.resolve() in excluded, f"{p.name} missing from _EXCLUDED_PATHS"


def test_excluded_tables_contains_the_seven_logs_tables_automation_runs_stays_included():
    """DB mirror of the two file-level pins above (plan 4c, tasks 1+2/3): now that
    RUN_HISTORY_JSON/MODEL_TELEMETRY_JSON are DB-backed (``PayloadTableStore``),
    ``ai.checkpoint._EXCLUDED_TABLES`` is the table-level counterpart to
    ``_EXCLUDED_PATHS`` this file exists to pin. automation_runs is the ONE sibling
    that stays checkpoint-INCLUDED (already portable/captured today, "durable run
    history") — the same design split ``tests/db/test_logs_tables.py`` already pins at
    the schema level, confirmed here too since this file is the cross-wave home for
    "a runtime bookkeeping write must not mint a checkpoint"."""
    from backend.ai.checkpoint import _EXCLUDED_TABLES

    for name in (
        "automation_runtime",
        "batch_ledger",
        "mcp_write_log",
        "model_telemetry",
        "pending_analysis",
        "run_history",
        "tool_call_log",
    ):
        assert name in _EXCLUDED_TABLES, f"{name} missing from _EXCLUDED_TABLES"
    assert "automation_runs" not in _EXCLUDED_TABLES


def test_excluded_tables_contains_the_misc_tables():
    """Storage plan 4E task 1: ``artifacts``/``attachments`` (chat-artifact & attachment
    metadata — ``attachments`` renamed from ``uploads`` at revision 0041, cross-channel-
    attachments RFC Phase 2) and ``pending_pushes`` (the INT6 push-retry queue) became DB
    tables, so their checkpoint exclusion — a rendered/runtime byproduct a data restore
    must never resurrect or delete — is now pinned at the table level, the successor to
    the retired ARTIFACTS_JSON/UPLOADS_JSON/PENDING_PUSHES_JSON _EXCLUDED_PATHS entries.
    ``email_snapshots`` is deliberately checkpoint-INCLUDED (real ingest data, portable)
    and must NOT appear."""
    from backend.ai.checkpoint import _EXCLUDED_TABLES

    for name in ("artifacts", "attachments", "pending_pushes"):
        assert name in _EXCLUDED_TABLES, f"{name} missing from _EXCLUDED_TABLES"
    assert "email_snapshots" not in _EXCLUDED_TABLES
