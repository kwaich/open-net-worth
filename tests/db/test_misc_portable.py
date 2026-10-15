"""The 4 misc stores join portable export/import and ``/api/reset`` (plan 4e, task 1).

Mirrors ``tests/db/test_logs_portable.py``/``tests/db/test_configs_portable.py``'s
per-store SPLIT along the portable axis (``portable_bridge.py``'s own module docstring,
PLAN 4E TASK 1 section):

1. **THREE join BRIDGED**, undoable, no cross-file FK — ``artifacts``, ``uploads``,
   ``email_snapshots`` — all three were ALREADY LIVE in ``core.portable.PORTABLE_FILES``
   before this task touched anything (the load-bearing safety check this task's brief
   calls out explicitly — the exact silent-data-loss bug class Wave A caught for
   ``income_events.json``). All three ride the GENERIC per-file
   ``services.portability._apply_bridged_import`` loop.
2. **ONE is wipe-only, never bridged** — ``pending_pushes`` — a runtime retry queue, not
   user data, correctly ABSENT from ``PORTABLE_FILES`` (mirrors ``asset_briefs.json``'s
   own wipe-only precedent).

``checkpoints`` is untouched by this task (schema-only; plan 4e task 2 owns its
portable/reset wiring, if any).

NO money-guard tests this wave (``misc_io.py``'s own module docstring — none of the four
stores holds a net-worth-bearing amount).
"""

from __future__ import annotations

import hashlib
import io
import json
import zipfile

import pytest
from sqlalchemy import select

from backend.ai import checkpoint as ckpt
from backend.core import portable
from backend.db import misc_io as mio
from backend.db import portable_bridge
from backend.db.engine import get_engine
from backend.db.tables import artifacts, attachments, email_snapshots, pending_pushes
from backend.services import portability as svc

# "uploads"/"uploads.json" -> "attachments"/"attachments.json" (revision 0041,
# cross-channel-attachments RFC Phase 2). This file exercises CURRENT (post-head)
# portable_bridge/misc_io/checkpoint behavior throughout, not revision 0018 in isolation,
# so every table/filename reference below tracks the renamed store.
BRIDGED_MISC_FILES = ("artifacts.json", "attachments.json", "email_snapshots.json")
WIPE_ONLY_MISC_FILES = ("pending_pushes.json",)

ARTIFACTS = [
    {
        "id": "art_1",
        "created_at": "2026-08-01T09:00:00Z",
        "session_id": "sess_1",
        "title": "May 2026 spending",
        "filename": "may_2026.html",
        "artifact_type": "html",
        "size_bytes": 1234,
        "file_path": "artifacts/art_1.html",
    }
]

UPLOADS = [
    {
        # A real-shaped legacy id: an attachment's files are named after its id, so a restore
        # refuses any id the store could not have minted (`up_`/`att_` + 12 hex).
        "id": "up_0123456789ab",
        "created_at": "2026-08-01T00:00:00Z",
        "filename": "first.csv",
        "kind": "text",
        "summary": "CSV file 'first.csv' with 1 rows",
        "size_bytes": 64,
        "format": "csv",
        "statement_id": None,
        "last_used_at": None,
        "last_used_session": None,
    }
]

EMAIL_SNAPSHOTS = [
    {
        "message_id": "<m-1@x>",
        "subject": "Your statement is ready",
        "from_addr": "alerts@bank.example",
        "received_at": "2026-08-01",
        "body_text": "Amount: $50.00 charged to your card.",
        "referenced": True,
        "stashed_at": "2026-08-01T00:00:00Z",
    }
]

PENDING_PUSHES = [
    {
        "id": "ppush_1",
        "message": "Analysis — AAPL",
        "channels": ["telegram"],
        "label": "Apple",
        "ticker": "AAPL",
        "attempts": 0,
        "next_attempt_at": "2026-08-01T00:05:00+00:00",
        "created_at": "2026-08-01T00:00:00+00:00",
    }
]


def _seed_three_bridged(conn) -> None:
    mio.import_payload_list(conn, artifacts, ARTIFACTS)
    mio.import_payload_list(conn, attachments, UPLOADS)
    mio.import_payload_list(conn, email_snapshots, EMAIL_SNAPSHOTS, id_field="message_id")


def _seed_wipe_only(conn) -> None:
    mio.import_payload_list(conn, pending_pushes, PENDING_PUSHES)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _build_bundle(files: dict[str, bytes]) -> bytes:
    """A hand-built bundle — mirrors every prior wave's own identical helper."""
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


# ── The registry itself: three bridged + wipe-able, one wipe-only ──────────────────────


def test_three_misc_filenames_are_bridged_with_wipe_fns():
    for filename in BRIDGED_MISC_FILES:
        assert filename in portable_bridge.BRIDGED, f"{filename} missing from BRIDGED"
        assert filename in portable_bridge._WIPE_FNS, f"{filename} missing from _WIPE_FNS"
        bridge = portable_bridge.BRIDGED[filename]
        assert callable(bridge.export_fn)
        assert callable(bridge.import_fn)


def test_pending_pushes_is_wipe_only_never_bridged():
    assert "pending_pushes.json" not in portable_bridge.BRIDGED
    assert "pending_pushes.json" in portable_bridge._WIPE_FNS


def test_three_misc_files_are_live_in_portable_files():
    """THE load-bearing safety check this task's brief names explicitly: every bridged
    store /api/reset's wipe_all unconditionally reaches MUST also be in
    core.portable.PORTABLE_FILES, or its pre-wipe backup silently omits it."""
    names = {p.name for p in portable.PORTABLE_FILES}
    for filename in BRIDGED_MISC_FILES:
        assert filename in names, f"{filename} expected LIVE in PORTABLE_FILES"


def test_pending_pushes_is_absent_from_portable_files():
    names = {p.name for p in portable.PORTABLE_FILES}
    assert "pending_pushes.json" not in names


def test_misc_files_constant_matches_the_bridge_registry():
    assert set(mio.MISC_FILES) == set(BRIDGED_MISC_FILES) | set(WIPE_ONLY_MISC_FILES)
    for filename in BRIDGED_MISC_FILES:
        assert filename in portable_bridge.BRIDGED
    for filename in WIPE_ONLY_MISC_FILES:
        assert filename not in portable_bridge.BRIDGED


# ── Export: DB-sourced, under the original filenames ──────────────────────────────────


def test_export_bundle_contains_the_three_live_misc_files_db_sourced(db_isolated):
    with get_engine().begin() as conn:
        _seed_three_bridged(conn)

    bundle = svc.build_export_bundle()

    with zipfile.ZipFile(io.BytesIO(bundle)) as zf:
        names = set(zf.namelist())
        assert set(BRIDGED_MISC_FILES) <= names
        assert "pending_pushes.json" not in names, "must never travel in an export bundle"
        assert json.loads(zf.read("artifacts.json")) == ARTIFACTS
        assert json.loads(zf.read("attachments.json")) == UPLOADS
        assert json.loads(zf.read("email_snapshots.json")) == EMAIL_SNAPSHOTS


# ── Import: representative bundle, old-bundle restore, absent-resets-to-empty ─────────


def test_misc_bundle_round_trips_through_bridge(db_isolated):
    with get_engine().begin() as conn:
        _seed_three_bridged(conn)

    bundle = svc.build_export_bundle()
    result = svc.apply_import(bundle)

    assert set(result["restored_files"]) >= set(BRIDGED_MISC_FILES)
    assert result["row_counts"]["artifacts.json"] == len(ARTIFACTS)
    assert result["row_counts"]["attachments.json"] == len(UPLOADS)
    assert result["row_counts"]["email_snapshots.json"] == len(EMAIL_SNAPSHOTS)

    with get_engine().connect() as conn:
        assert mio.export_payload_list(conn, artifacts) == ARTIFACTS
        assert mio.export_payload_list(conn, attachments) == UPLOADS
        assert mio.export_payload_list(conn, email_snapshots) == EMAIL_SNAPSHOTS


def test_import_synthetic_old_bundle_lands_rows(db_isolated):
    """A hand-built bundle stands in for one exported BEFORE these stores were
    migrated — old and new bundles are handled identically, by filename alone."""
    bundle = _build_bundle(
        {
            "artifacts.json": json.dumps(ARTIFACTS, ensure_ascii=False).encode(),
            "email_snapshots.json": json.dumps(EMAIL_SNAPSHOTS, ensure_ascii=False).encode(),
        }
    )

    result = svc.apply_import(bundle)

    assert result["row_counts"]["artifacts.json"] == len(ARTIFACTS)
    assert result["row_counts"]["email_snapshots.json"] == len(EMAIL_SNAPSHOTS)
    with get_engine().connect() as conn:
        assert mio.export_payload_list(conn, artifacts) == ARTIFACTS
        assert mio.export_payload_list(conn, email_snapshots) == EMAIL_SNAPSHOTS


def test_reimport_replaces_existing_misc_data_entirely(db_isolated):
    with get_engine().begin() as conn:
        mio.import_payload_list(conn, artifacts, ARTIFACTS)

    new_artifacts = [{**ARTIFACTS[0], "id": "art_2", "title": "replaced"}]
    bundle = _build_bundle({"artifacts.json": json.dumps(new_artifacts).encode()})
    result = svc.apply_import(bundle)

    assert result["row_counts"]["artifacts.json"] == len(new_artifacts)
    with get_engine().connect() as conn:
        assert mio.export_payload_list(conn, artifacts) == new_artifacts


def test_import_absent_misc_file_wipes_that_store_to_empty(db_isolated):
    with get_engine().begin() as conn:
        mio.import_payload_list(conn, artifacts, ARTIFACTS)

    bundle = _build_bundle({"attachments.json": json.dumps(UPLOADS, ensure_ascii=False).encode()})
    # artifacts.json intentionally absent.
    result = svc.apply_import(bundle)

    assert "artifacts.json" not in result["restored_files"]
    with get_engine().connect() as conn:
        assert mio.export_payload_list(conn, artifacts) == []


def test_bundle_containing_pending_pushes_is_rejected_as_unknown(db_isolated):
    """pending_pushes.json in a hand-built bundle gets the SAME treatment any other
    non-portable filename already gets — a runtime queue must never be accepted as a
    bundle entry."""
    from backend.core.errors import ValidationError

    bundle = _build_bundle({"pending_pushes.json": json.dumps(PENDING_PUSHES).encode()})

    report = svc.inspect_bundle(bundle)
    assert report["ok"] is False
    assert any("pending_pushes.json" in e and "unknown" in e for e in report["errors"])

    with pytest.raises(ValidationError):
        svc.apply_import(bundle)


# ── /api/reset — all four misc tables, bridged or wipe-only ────────────────────────────


def test_reset_wipes_all_four_misc_tables(db_isolated):
    with get_engine().begin() as conn:
        _seed_three_bridged(conn)
        _seed_wipe_only(conn)

    svc.reset_bridged_stores()

    with get_engine().connect() as conn:
        assert mio.export_payload_list(conn, artifacts) == []
        assert mio.export_payload_list(conn, attachments) == []
        assert mio.export_payload_list(conn, email_snapshots) == []
        assert mio.export_payload_list(conn, pending_pushes) == []


def _reset_client(tmp_path, monkeypatch):
    """Shared TestClient setup — mirrors every prior wave's own identical helper."""
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


def test_reset_endpoint_wipes_misc_tables_end_to_end(db_isolated, tmp_path, monkeypatch):
    with get_engine().begin() as conn:
        mio.import_payload_list(conn, artifacts, ARTIFACTS)
        mio.import_payload_list(conn, pending_pushes, PENDING_PUSHES)

    client, _data = _reset_client(tmp_path, monkeypatch)
    r = client.post("/api/reset")

    assert r.status_code == 200
    assert r.json()["ok"] is True
    with get_engine().connect() as conn:
        assert mio.export_payload_list(conn, artifacts) == []
        assert mio.export_payload_list(conn, pending_pushes) == []


def test_reset_backup_snapshot_contains_misc_rows_but_never_pending_pushes(
    db_isolated, tmp_path, monkeypatch
):
    """Regression-shaped proof (mirrors every prior wave's own reset-backup test) that
    /api/reset's pre-wipe backup loop actually captures the three bridged misc stores'
    real rows — and never backs up the wipe-only pending_pushes queue."""
    from backend.paths import ARTIFACTS_JSON, ATTACHMENTS_JSON, EMAIL_SNAPSHOTS_JSON

    with get_engine().begin() as conn:
        _seed_three_bridged(conn)
        _seed_wipe_only(conn)

    client, data = _reset_client(tmp_path, monkeypatch)
    monkeypatch.setattr(
        portable,
        "PORTABLE_FILES",
        [*portable.PORTABLE_FILES, ARTIFACTS_JSON, ATTACHMENTS_JSON, EMAIL_SNAPSHOTS_JSON],
    )

    r = client.post("/api/reset")
    assert r.status_code == 200

    backup_dirs = list((data / "backups").iterdir())
    assert len(backup_dirs) == 1
    backup_dir = backup_dirs[0]

    assert json.loads((backup_dir / "artifacts.json").read_text()) == ARTIFACTS
    assert json.loads((backup_dir / "attachments.json").read_text()) == UPLOADS
    assert json.loads((backup_dir / "email_snapshots.json").read_text()) == EMAIL_SNAPSHOTS
    assert not (backup_dir / "pending_pushes.json").exists()

    with get_engine().connect() as conn:
        assert mio.export_payload_list(conn, artifacts) == []


# ── pending_pushes: wiped, but a checkpoint no-op (checkpointed=False) ─────────────────


def test_pending_pushes_wipe_reaches_the_row_despite_checkpointed_false(db_isolated):
    """pending_pushes' STORE construction opts out of the hook (checkpointed=False), but
    the RESET wipe path goes through portable_bridge's OWN ``_capture_then_delete``
    helper — a different call site — which fires ``fire_db_write_hook`` unconditionally,
    uniform with every sibling wipe fn. Either way the row is gone after reset, and
    ``ai.checkpoint._EXCLUDED_TABLES`` makes any capture a structural no-op regardless.
    ``email_snapshots`` is the ONE non-excluded misc sibling (artifacts/attachments are
    ALSO excluded — module docstring, PLAN 4E TASK 1 section), so it is the correct
    "must be captured" control here, not artifacts/attachments."""
    assert "pending_pushes" in ckpt._EXCLUDED_TABLES
    assert "artifacts" in ckpt._EXCLUDED_TABLES
    assert "attachments" in ckpt._EXCLUDED_TABLES
    assert "email_snapshots" not in ckpt._EXCLUDED_TABLES

    with get_engine().begin() as conn:
        _seed_wipe_only(conn)
        _seed_three_bridged(conn)  # includes email_snapshots — the non-excluded sibling

    turn_ctx, token = ckpt.begin_turn(
        "sess-misc-t1-excl", anchor_history_len=0, message_preview="r"
    )
    try:
        svc.reset_bridged_stores()
    finally:
        entry = ckpt.end_turn(turn_ctx, token)

    with get_engine().connect() as conn:
        assert mio.export_payload_list(conn, pending_pushes) == []  # reset means reset
        assert mio.export_payload_list(conn, artifacts) == []
        assert mio.export_payload_list(conn, email_snapshots) == []

    assert entry is not None
    assert "email_snapshots" in entry["db_tables"], "a non-excluded misc wipe must be captured"
    assert "pending_pushes" not in entry["db_tables"], (
        "pending_pushes' capture must be a structural no-op, not merely untested"
    )
    assert "artifacts" not in entry["db_tables"], (
        "artifacts' capture must be a structural no-op, not merely untested"
    )
    assert "attachments" not in entry["db_tables"], (
        "attachments' capture must be a structural no-op, not merely untested"
    )


# ── Undoable misc imports (this task's binding requirement) ────────────────────────────


def test_misc_import_is_undoable_via_checkpoint_restore(db_isolated):
    """At least one misc store's import is undoable: the write_log before-images a
    checkpoint turn collects let restore() revert it. Uses ``email_snapshots`` — the ONE
    misc store this wave that stays checkpoint-INCLUDED — since artifacts/uploads/
    pending_pushes are all EXCLUDED (module docstring, PLAN 4E TASK 1 section) and would
    never mint a capturable entry."""
    with get_engine().begin() as conn:
        mio.import_payload_list(conn, email_snapshots, EMAIL_SNAPSHOTS, id_field="message_id")

    new_snapshots = [{**EMAIL_SNAPSHOTS[0], "message_id": "<m-2@x>", "subject": "replaced"}]
    bundle = _build_bundle({"email_snapshots.json": json.dumps(new_snapshots).encode()})

    turn_ctx, token = ckpt.begin_turn(
        "sess-misc-t1-undo", anchor_history_len=0, message_preview="i"
    )
    try:
        svc.apply_import(bundle)
    finally:
        entry = ckpt.end_turn(turn_ctx, token)

    assert entry is not None
    assert "email_snapshots" in entry["db_tables"]

    with get_engine().connect() as conn:
        assert mio.export_payload_list(conn, email_snapshots) == new_snapshots

    ckpt.restore(entry["id"], mode="data")

    with get_engine().connect() as conn:
        assert mio.export_payload_list(conn, email_snapshots) == EMAIL_SNAPSHOTS


# ── select() import used only to prove the misc tables carry no book_id FK ─────────────


def test_pending_pushes_row_has_no_book_id_column(db_isolated):
    with get_engine().begin() as conn:
        _seed_wipe_only(conn)
    with get_engine().connect() as conn:
        row = conn.execute(select(pending_pushes)).first()
    assert not hasattr(row, "book_id")
