"""automation_runs joins portable export/import and ``/api/reset`` (plan 4c, task 3) —
the ONE logs/telemetry store that is checkpoint-INCLUDED and reset-scoped. Its 7
siblings (automation_runtime/batch_ledger/mcp_write_log/
model_telemetry/pending_analysis/run_history/tool_call_log) are runtime/behavioral
logs: checkpoint-EXCLUDED (already pinned by
``tests/db/test_logs_tables.py::test_automation_runs_is_checkpoint_included_its_seven_siblings_are_excluded``,
task 1) AND — this task's own binding — deliberately NOT bridged, NOT in
``PORTABLE_FILES``, and NOT reached by ``wipe_all``: a behavior-preserving port, not a
new policy (none of the 7 were portable or reset-scoped before this task touched
anything either).
(Originally 8 siblings/9-store classification including harness_suggestions — dropped
to 7/8 on 2026-08-28 when self-harness was removed, see docs/launch/BOARD.md.)

Mirrors ``tests/db/test_investments_portable.py``'s shape, sourced from
``backend.db.logs_io`` (task 2) via ``backend.db.portable_bridge``:

1. **Undoable** — ``logs_io.import_payload_list`` fires ``core.io.fire_db_write_hook``
   unconditionally (task 2), so a restore through ``ai.checkpoint`` reverts an
   ``automation_runs`` import exactly like a money/planning/investment one.
2. **No cross-file FK** (``tables.py``'s own Tier-2 logs comment — no book_id, no FK
   anywhere in this wave), so the one BRIDGED entry rides the GENERIC per-file
   ``services.portability._apply_bridged_import`` loop — no group orchestration.

THE STORE CLASSIFICATION (this task's controller-mandated deliverable — mirrors
task-3-brief.md's own table): automation_runs is REAL, user-visible run history —
``AUTOMATION_RUNS_JSON``'s own file comment already says "portable", and it was
ALREADY a member of ``core.portable.PORTABLE_FILES`` before this task touched
anything (only its ``BRIDGED`` wiring is new — the same "dead file path -> DB bridge"
fix every prior wave's first LIVE table needed). The other 7 are runtime scheduler
state / re-fetchable / observability rings — none of them were in ``PORTABLE_FILES``
before this task, and this task does NOT add them (see the module-level "excluded"
tests below) — extending "reset means reset" to them is a deferred ADR-worthy
decision, not a silent side effect of this storage-engine port.
"""

from __future__ import annotations

import hashlib
import io
import json
import zipfile

import pytest

from backend.ai import checkpoint as ckpt
from backend.core import portable
from backend.db import logs_io as lio
from backend.db import portable_bridge
from backend.db.engine import get_engine
from backend.db.tables import (
    automation_runs,
    automation_runtime,
    batch_ledger,
    mcp_write_log,
    model_telemetry,
    pending_analysis,
    run_history,
    tool_call_log,
)
from backend.services import portability as svc

ALL_LOGS_FILES = (
    "automation_runs.json",
    "automation_runtime.json",
    "batch_ledger.json",
    "mcp_write_log.json",
    "model_telemetry.json",
    "pending_analysis.json",
    "run_history.json",
    "tool_call_log.json",
)
# The 7 runtime/behavioral logs — checkpoint-excluded, NOT bridged, NOT portable.
EXCLUDED_LOGS_FILES = tuple(f for f in ALL_LOGS_FILES if f != "automation_runs.json")
EXCLUDED_LOGS_TABLES = (
    automation_runtime,
    batch_ledger,
    mcp_write_log,
    model_telemetry,
    pending_analysis,
    run_history,
    tool_call_log,
)

# ── Fixtures — small but non-trivial (mirrors test_logs_cutover.py's own shapes,
# task 2's proven-valid round-trip fixtures, trimmed to one row per store) ──────────

AUTOMATION_RUNS = [
    {
        "id": "run_1",
        "automation_id": "auto_1",
        "session_id": "sess_1",
        "status": "completed",
        "trigger": {"kind": "schedule", "cron": "0 9 * * *"},
        "execution": {"mode": "autonomous"},
        "model": "claude-haiku-4-5",
        "effort": "medium",
        "started_at": "2026-08-01T09:00:00Z",
        "finished_at": "2026-08-01T09:00:42Z",
        "summary": "Checked 3 accounts, no anomalies — café ☕ unicode round-trip",
        "error": None,
        "steps": [{"tool": "list_accounts", "ok": True}],
        "proposed_changes": [],
        "pending_tool_ids": [],
        "context": {},
        "files": [],
        "working_dir": None,
        "input_tokens": 512,
        "output_tokens": 128,
        "cache_creation_tokens": 0,
        "cache_read_tokens": 0,
        "cost_usd": 0.0031,
        "stats": {"tool_calls": 1},
    }
]

AUTOMATION_RUNTIME = [{"automation_id": "auto_1", "state": {"owner": "sched-1"}}]
BATCH_LEDGER = [
    {
        "handle": "batch-001",
        "task": "intel",
        "transport": "anthropic",
        "source": "intel_scan",
        "model": "claude-haiku-4-5",
        "custom_ids": ["intel-abc123"],
        "submitted_at": "2026-08-01T00:00:00Z",
        "status": "pending",
        "poll_count": 0,
        "last_polled_at": None,
        "metadata": {},
    }
]
MCP_WRITE_LOG = [
    {
        "id": "mcpw_1",
        "ts": "2026-08-01T00:00:00Z",
        "surface": "api_token",
        "tool": "create_transaction",
        "domains": ["ledger"],
        "args_summary": "1 field",
        "base_amount": 42.5,
        "checkpoint_id": "ckpt_1",
        "result_ok": True,
    }
]
MODEL_TELEMETRY = {"claude-haiku-4-5": {"samples": [], "errors": 0, "total": 1}}
PENDING_ANALYSIS = [
    {
        "id": "pan_1",
        "subject": "crash",
        "ticker": "AAPL",
        "signal_id": "sig_1",
        "trigger": "crash_detected",
        "triggers": ["crash_detected"],
        "payload": {"note": "price dropped 8%"},
        "status": "pending",
        "attempts": 0,
        "created_at": "2026-08-01T00:00:00Z",
    }
]
RUN_HISTORY = [
    {
        "id": "run_hist_1",
        "kind": "agent",
        "entity_id": "agt_1",
        "entity_name": "Lucius",
        "started_at": "2026-08-01T00:00:00Z",
        "finished_at": "2026-08-01T00:00:10Z",
        "duration_ms": 10000,
        "status": "ok",
        "error": None,
        "summary": "ran fine",
        "trigger": "manual",
        "finding_id": None,
        "steps": [],
        "cost_usd": 0.002,
    }
]
TOOL_CALL_LOG = [
    {
        "id": "tcall_1",
        "ts": "2026-08-01T00:00:00Z",
        "event": "call",
        "tool": "list_accounts",
        "alias_used": None,
        "origin": "web",
        "session_id": "sess_1",
        "turn_index": 0,
        "call_index": 0,
        "mutated": False,
        "domains": [],
        "ok": True,
        "error_class": None,
        "duration_ms": 12.5,
        "result_chars": 128,
        "input_chars": 4,
        "checkpoint_id": None,
        "gate_outcome": None,
        "gate_args_valid": None,
    }
]


def _seed_automation_runs(conn) -> None:
    lio.import_payload_list(conn, automation_runs, AUTOMATION_RUNS)


def _seed_all_seven_excluded(conn) -> None:
    lio.import_payload_list(conn, automation_runtime, AUTOMATION_RUNTIME, id_field="automation_id")
    lio.import_payload_list(conn, batch_ledger, BATCH_LEDGER, id_field="handle")
    lio.import_payload_list(conn, mcp_write_log, MCP_WRITE_LOG)
    lio.import_model_telemetry(conn, MODEL_TELEMETRY)
    lio.import_payload_list(conn, pending_analysis, PENDING_ANALYSIS)
    lio.import_payload_list(conn, run_history, RUN_HISTORY)
    lio.import_payload_list(conn, tool_call_log, TOOL_CALL_LOG)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _build_bundle(files: dict[str, bytes]) -> bytes:
    """A hand-built bundle — mirrors test_investments_portable.py's/
    test_planning_portable.py's own identical helper."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        entries = {}
        for name, data in files.items():
            zf.writestr(name, data)
            entries[name] = {"sha256": _sha(data)}
        zf.writestr(
            "manifest.json",
            json.dumps({"schema_version": svc.SCHEMA_VERSION, "entries": entries}),
        )
    return buf.getvalue()


# ── The registry itself: automation_runs bridged + wipe-able; its 8 siblings are
# in NEITHER BRIDGED NOR _WIPE_FNS at all (not even wipe-only, unlike asset_brief in
# plan 4b — there is no reset role for these 8, full stop). ─────────────────────────


def test_automation_runs_is_bridged_with_a_wipe_fn():
    assert "automation_runs.json" in portable_bridge.BRIDGED
    assert "automation_runs.json" in portable_bridge._WIPE_FNS
    bridge = portable_bridge.BRIDGED["automation_runs.json"]
    assert callable(bridge.export_fn)
    assert callable(bridge.import_fn)


def test_eight_excluded_logs_files_are_neither_bridged_nor_wiped():
    """Unlike asset_brief (plan 4b: wipe-only, no BRIDGED entry), these 8 have NO reset
    role at all — behavior-preserving port, not a new policy (module docstring)."""
    for filename in EXCLUDED_LOGS_FILES:
        assert filename not in portable_bridge.BRIDGED, f"{filename} must not be bridged"
        assert filename not in portable_bridge._WIPE_FNS, f"{filename} must not be wipe-able"


def test_automation_runs_is_live_in_portable_files():
    """AUTOMATION_RUNS_JSON was ALREADY a PORTABLE_FILES member before this task (module
    docstring) — only its BRIDGED wiring is new, so the pre-wipe backup loop
    (api/setup.py::reset_all_data) starts reaching its real DB rows instead of a dead
    file path."""
    names = {p.name for p in portable.PORTABLE_FILES}
    assert "automation_runs.json" in names


def test_eight_excluded_logs_files_are_not_in_portable_files():
    names = {p.name for p in portable.PORTABLE_FILES}
    for filename in EXCLUDED_LOGS_FILES:
        assert filename not in names, f"{filename} must not be portable"


# ── automation_runs.json: round-trip through the bridge directly (export -> wipe ->
# import -> equal) — the exact shape ambiguity-resolution item asked for. ───────────


def test_automation_runs_round_trips_through_the_bridge_export_wipe_import_equal(db_isolated):
    bridge = portable_bridge.BRIDGED["automation_runs.json"]
    with get_engine().begin() as conn:
        _seed_automation_runs(conn)

    with get_engine().connect() as conn:
        exported = bridge.export_fn(conn)
    assert exported == AUTOMATION_RUNS

    with get_engine().begin() as conn:
        portable_bridge.wipe(conn, "automation_runs.json")
    with get_engine().connect() as conn:
        assert bridge.export_fn(conn) == []

    with get_engine().begin() as conn:
        n = bridge.import_fn(conn, exported)
    assert n == len(AUTOMATION_RUNS)
    with get_engine().connect() as conn:
        assert bridge.export_fn(conn) == AUTOMATION_RUNS


# ── Export: DB-sourced, under the original filename ──────────────────────────────


def test_export_bundle_contains_automation_runs_db_sourced(db_isolated):
    with get_engine().begin() as conn:
        _seed_automation_runs(conn)
        _seed_all_seven_excluded(conn)

    bundle = svc.build_export_bundle()

    with zipfile.ZipFile(io.BytesIO(bundle)) as zf:
        names = set(zf.namelist())
        assert "automation_runs.json" in names
        for filename in EXCLUDED_LOGS_FILES:
            assert filename not in names, f"{filename} must never travel in an export bundle"
        assert json.loads(zf.read("automation_runs.json")) == AUTOMATION_RUNS
        runs_bytes = zf.read("automation_runs.json")

    # Byte-format check: matches JsonStore._save_raw's own convention exactly, like
    # every other bridged store's own equivalent assertion.
    with get_engine().connect() as conn:
        expected = json.dumps(
            lio.export_payload_list(conn, automation_runs), ensure_ascii=False, indent=2
        ).encode("utf-8")
    assert runs_bytes == expected


# ── Import: bundle round-trip, incl. old synthetic bundle, reimport-replaces,
# absent-file-wipes-to-empty, and unknown-file rejection for each of the 8 excluded ──


def test_automation_runs_bundle_round_trips_through_bridge(db_isolated):
    with get_engine().begin() as conn:
        _seed_automation_runs(conn)

    bundle = svc.build_export_bundle()
    result = svc.apply_import(bundle)

    assert "automation_runs.json" in result["restored_files"]
    assert result["row_counts"]["automation_runs.json"] == len(AUTOMATION_RUNS)
    with get_engine().connect() as conn:
        assert lio.export_payload_list(conn, automation_runs) == AUTOMATION_RUNS


def test_import_synthetic_old_bundle_lands_rows_for_automation_runs(db_isolated):
    """A hand-built bundle stands in for one exported BEFORE this store was migrated —
    old and new bundles are handled identically, by filename alone (mirrors
    test_investments_portable.py's/test_planning_portable.py's identical-shaped test)."""
    bundle = _build_bundle({"automation_runs.json": json.dumps(AUTOMATION_RUNS).encode()})

    result = svc.apply_import(bundle)

    assert result["row_counts"]["automation_runs.json"] == len(AUTOMATION_RUNS)
    with get_engine().connect() as conn:
        assert lio.export_payload_list(conn, automation_runs) == AUTOMATION_RUNS


def test_reimport_replaces_existing_automation_runs_entirely(db_isolated):
    with get_engine().begin() as conn:
        _seed_automation_runs(conn)

    new_runs = [{**AUTOMATION_RUNS[0], "id": "run_2", "status": "failed"}]
    bundle = _build_bundle({"automation_runs.json": json.dumps(new_runs).encode()})
    result = svc.apply_import(bundle)

    assert result["row_counts"]["automation_runs.json"] == len(new_runs)
    with get_engine().connect() as conn:
        assert lio.export_payload_list(conn, automation_runs) == new_runs


def test_import_absent_automation_runs_file_wipes_that_store_to_empty(db_isolated):
    with get_engine().begin() as conn:
        _seed_automation_runs(conn)

    # automation_runs.json intentionally absent from an otherwise-valid, all-portable
    # bundle (budget_assignments.json is a live PORTABLE_FILES entry unrelated to this
    # wave) — the generic loop must still reset automation_runs to empty.
    bundle = _build_bundle({"budget_assignments.json": json.dumps({}).encode()})
    result = svc.apply_import(bundle)

    assert "automation_runs.json" not in result["restored_files"]
    with get_engine().connect() as conn:
        assert lio.export_payload_list(conn, automation_runs) == []


@pytest.mark.parametrize("filename", EXCLUDED_LOGS_FILES)
def test_bundle_containing_an_excluded_logs_file_is_rejected_as_unknown(db_isolated, filename):
    """Mirrors test_bundle_containing_asset_brief_file_is_rejected_as_unknown — none of
    the 8 excluded logs filenames may ever be accepted as a bundle entry, exactly like
    any other non-portable filename."""
    payload = {} if filename == "model_telemetry.json" else []
    bundle = _build_bundle({filename: json.dumps(payload).encode()})

    report = svc.inspect_bundle(bundle)
    assert report["ok"] is False
    assert any(filename in e and "unknown" in e for e in report["errors"])

    from backend.core.errors import ValidationError

    with pytest.raises(ValidationError):
        svc.apply_import(bundle)


# ── /api/reset — automation_runs wiped, the 8 excluded logs stores UNTOUCHED ────────


def test_reset_wipes_automation_runs_the_seven_excluded_logs_stores_are_untouched(db_isolated):
    """THE reset-smoke test (ambiguity-resolution item d): seed a row in EACH of the 7
    excluded tables plus one in automation_runs, call reset_bridged_stores(), assert
    the 7 are UNTOUCHED and automation_runs is EMPTY — proves the "behavior-preserving
    port, not a new policy" claim end to end, not just by registry inspection."""
    with get_engine().begin() as conn:
        _seed_automation_runs(conn)
        _seed_all_seven_excluded(conn)

    svc.reset_bridged_stores()

    with get_engine().connect() as conn:
        assert lio.export_payload_list(conn, automation_runs) == []
        # The 7 excluded stores must be byte-for-byte untouched by a reset.
        assert lio.export_payload_list(conn, automation_runtime) == AUTOMATION_RUNTIME
        assert lio.export_payload_list(conn, batch_ledger) == BATCH_LEDGER
        assert lio.export_payload_list(conn, mcp_write_log) == MCP_WRITE_LOG
        assert lio.export_model_telemetry(conn) == MODEL_TELEMETRY
        assert lio.export_payload_list(conn, pending_analysis) == PENDING_ANALYSIS
        assert lio.export_payload_list(conn, run_history) == RUN_HISTORY
        assert lio.export_payload_list(conn, tool_call_log) == TOOL_CALL_LOG


def _reset_client(tmp_path, monkeypatch):
    """Shared TestClient setup — mirrors test_investments_portable.py's/
    test_planning_portable.py's own identical helper."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend import paths as bpaths
    from backend.api import setup as setup_api

    data = tmp_path / "data"
    sessions = data / "sessions"
    sessions.mkdir(parents=True)
    profile = data / "profile.json"
    profile.write_text(json.dumps({"name": "Alex"}))

    monkeypatch.setattr(portable, "PORTABLE_FILES", [profile])
    monkeypatch.setattr(bpaths, "DATA_DIR", data)
    monkeypatch.setattr(bpaths, "SESSIONS_DIR", sessions)
    monkeypatch.setattr(bpaths, "CONNECTOR_SESSIONS_JSON", data / "connector_sessions.json")
    monkeypatch.setattr(bpaths, "PROFILE_JSON", profile)

    app = FastAPI()
    app.include_router(setup_api.router)
    return TestClient(app), data


def test_reset_endpoint_wipes_automation_runs_end_to_end(db_isolated, tmp_path, monkeypatch):
    with get_engine().begin() as conn:
        _seed_automation_runs(conn)

    client, _data = _reset_client(tmp_path, monkeypatch)
    r = client.post("/api/reset")

    assert r.status_code == 200
    assert r.json()["ok"] is True
    with get_engine().connect() as conn:
        assert lio.export_payload_list(conn, automation_runs) == []


def test_reset_backup_snapshot_contains_automation_runs_row(db_isolated, tmp_path, monkeypatch):
    """THE DATA-LOSS REGRESSION TEST (this task's own "confirm no /api/reset
    data-loss" requirement) — the exact shape of Wave A/B's own
    ``test_reset_backup_snapshot_contains_planning_rows``/
    ``test_reset_backup_snapshot_contains_portfolio_rows``: ``/api/reset``'s pre-wipe
    backup loop (``api/setup.py::reset_all_data``) calls ``export_bytes_for(src)`` for
    every ``core.portable.PORTABLE_FILES`` entry — which only reaches a DB-backed
    store's REAL rows once that filename is ALSO a ``portable_bridge.BRIDGED`` key.
    automation_runs is post-cutover (no file on disk — the DB is the only source of
    truth since plan 4c task 1/2), seeded with a real run row, reset is triggered, and
    the backup snapshot must contain it — a self-hoster's run history is never
    destroyed with no recovery path."""
    from backend.paths import AUTOMATION_RUNS_JSON

    with get_engine().begin() as conn:
        _seed_automation_runs(conn)

    client, data = _reset_client(tmp_path, monkeypatch)
    monkeypatch.setattr(
        portable, "PORTABLE_FILES", [*portable.PORTABLE_FILES, AUTOMATION_RUNS_JSON]
    )

    r = client.post("/api/reset")
    assert r.status_code == 200

    backup_dirs = list((data / "backups").iterdir())
    assert len(backup_dirs) == 1
    backup_dir = backup_dirs[0]

    runs_backup = backup_dir / "automation_runs.json"
    assert runs_backup.exists(), (
        "automation_runs.json missing from the pre-reset backup — a self-hoster's real "
        "run history would be destroyed with no recovery path"
    )
    assert json.loads(runs_backup.read_text()) == AUTOMATION_RUNS

    with get_engine().connect() as conn:
        assert lio.export_payload_list(conn, automation_runs) == []


# ── Undoable automation_runs imports (this task's binding requirement) ─────────────


def test_automation_runs_import_is_undoable_via_checkpoint_restore(db_isolated):
    """The import is undoable: the write_log before-images a checkpoint turn collects
    let restore() revert it. Mirrors
    test_investment_import_is_undoable_via_checkpoint_restore's minimal pattern
    (direct ai.checkpoint begin_turn/end_turn/restore — the file AND DB write hooks
    are already registered at backend.ai.checkpoint's own module-import time)."""
    with get_engine().begin() as conn:
        _seed_automation_runs(conn)

    new_runs = [{**AUTOMATION_RUNS[0], "id": "run_2", "status": "failed"}]
    bundle = _build_bundle({"automation_runs.json": json.dumps(new_runs).encode()})

    turn_ctx, token = ckpt.begin_turn(
        "sess-logs-t3-undo", anchor_history_len=0, message_preview="i"
    )
    try:
        svc.apply_import(bundle)
    finally:
        entry = ckpt.end_turn(turn_ctx, token)

    assert entry is not None
    assert "automation_runs" in entry["db_tables"]

    with get_engine().connect() as conn:
        assert lio.export_payload_list(conn, automation_runs) == new_runs

    ckpt.restore(entry["id"], mode="data")

    with get_engine().connect() as conn:
        assert lio.export_payload_list(conn, automation_runs) == AUTOMATION_RUNS
