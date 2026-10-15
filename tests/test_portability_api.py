"""P2-PT1 — export/import TestClient smoke + checkpoint-undo + reset regression.

Isolated on a tmp DATA_DIR (project rule). The checkpoint machinery is also
pointed at tmp so the pre-import checkpoint round-trips without touching live
checkpoint storage.
"""

from __future__ import annotations

import io
import json
import zipfile

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend import paths as bpaths
from backend.ai import checkpoint as ckpt
from backend.api import portability as api
from backend.api import setup as setup_api
from backend.core import io as core_io
from backend.core import portable
from backend.db.profile_store import ProfileStore
from backend.services import portability as svc


@pytest.fixture
def client(tmp_path, monkeypatch):
    data = tmp_path / "data"
    sessions = data / "sessions"
    sessions.mkdir(parents=True)
    blobs = tmp_path / "checkpoints" / "blobs"
    blobs.mkdir(parents=True)
    index = tmp_path / "checkpoints" / "index.json"

    # "profile.json" is now DB-bridged (portable_bridge.BRIDGED, Wave 5) — the Path below is
    # still the right identity for the manifest (only its .name is used for bridge lookup;
    # export/import routes through ProfileStore, never this dead file path), so it doubles
    # as this fixture's one "bridged, undoable" entry. Deliberately NOT "transactions.json"
    # (plan 3 task 6) NOR "commitments.json" (plan 4a task 7, this variable's ORIGINAL
    # filename — swapped out here for the identical reason once commitments migrated too):
    # both are now DB-bridged too. A Path merely named that way, monkeypatched into a
    # PORTABLE_FILES list that omits the money six's/planning's other filenames, would
    # get routed through services.portability's DB-bridge step and read/write a table
    # instead of this fixture's plain file — the variable/dict-key name "transactions"
    # stays unchanged below (this file never asserts on the literal string
    # "transactions.json"), only the on-disk filename moves. "rules.json" is a plain
    # list-rooted JsonStore file with no migration in flight — this fixture's one
    # "plain file, generic loop" entry.
    profile = data / "profile.json"
    transactions = data / "rules.json"
    ProfileStore().set_raw({"name": "Alex", "base_currency": "SGD"})
    transactions.write_text(json.dumps([{"id": "t1"}, {"id": "t2"}]))

    monkeypatch.setattr(portable, "PORTABLE_FILES", [profile, transactions])
    monkeypatch.setattr(portable, "PORTABLE_DIRS", [sessions])
    monkeypatch.setattr(svc, "_ALLOWED_FILENAMES", {p.name for p in portable.PORTABLE_FILES})

    # /api/reset references these from backend.paths at call time; keep them in tmp.
    # PROFILE_JSON is vestigial here (no code path reads it any more — reset_all_data's
    # blank-shape write is now the unconditional ProfileStore().set_raw(_RESET_PROFILE)
    # call below, not a file write) but harmless to still patch.
    monkeypatch.setattr(bpaths, "DATA_DIR", data)
    monkeypatch.setattr(bpaths, "SESSIONS_DIR", sessions)
    monkeypatch.setattr(bpaths, "CONNECTOR_SESSIONS_JSON", data / "connector_sessions.json")
    monkeypatch.setattr(bpaths, "PROFILE_JSON", profile)
    monkeypatch.setattr(bpaths, "WATCHLIST_JSON", data / "watchlist.json")

    # Checkpoint machinery → tmp.
    monkeypatch.setattr(ckpt, "DATA_DIR", data)
    monkeypatch.setattr(ckpt, "CHECKPOINTS_BLOBS", blobs)
    monkeypatch.setattr(ckpt, "CHECKPOINTS_INDEX", index)
    prev_hook = core_io._write_hook
    ckpt.register_write_hook(ckpt._snapshot_active)

    app = FastAPI()
    app.include_router(api.router)
    app.include_router(setup_api.router)
    c = TestClient(app)
    # GET /api/data/export now requires the X-Requested-With CSRF header (auth S4); the
    # SPA sends it on every same-origin XHR, so set it as a client default here.
    c.headers.update({"X-Requested-With": "XMLHttpRequest"})
    c._paths = {"profile": profile, "transactions": transactions, "sessions": sessions}
    yield c
    core_io.register_write_hook(prev_hook)


def test_export_returns_zip_attachment(client):
    r = client.get("/api/data/export")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/zip"
    assert "attachment" in r.headers["content-disposition"]
    assert ".zip" in r.headers["content-disposition"]
    # The streamed bytes are a real zip with the manifest.
    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        assert "manifest.json" in zf.namelist()


def test_import_validate_is_dry_run(client):
    bundle = client.get("/api/data/export").content
    client._paths["transactions"].write_text(json.dumps([{"id": "mutated"}]))
    r = client.post(
        "/api/data/import/validate", files={"file": ("b.zip", bundle, "application/zip")}
    )
    assert r.status_code == 200
    assert r.json()["ok"] is True
    # Dry-run wrote nothing.
    assert json.loads(client._paths["transactions"].read_text()) == [{"id": "mutated"}]


def test_import_validate_says_what_to_choose_when_the_file_is_not_a_zip(client):
    r = client.post(
        "/api/data/import/validate", files={"file": ("notes.zip", b"not a zip", "application/zip")}
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert body["errors"] == [
        "This file isn't an Open Net Worth backup. Choose the .zip you exported from "
        "Settings \u2192 Data."
    ]


def test_import_without_replace_is_dry_run(client):
    bundle = client.get("/api/data/export").content
    client._paths["transactions"].write_text(json.dumps([{"id": "mutated"}]))
    r = client.post("/api/data/import", files={"file": ("b.zip", bundle, "application/zip")})
    assert r.status_code == 200
    assert "undo_checkpoint_id" not in r.json()
    assert json.loads(client._paths["transactions"].read_text()) == [{"id": "mutated"}]


def test_import_replace_restores_and_creates_checkpoint(client):
    bundle = client.get("/api/data/export").content
    orig = client._paths["transactions"].read_text()
    client._paths["transactions"].write_text(json.dumps([{"id": "wrong"}]))

    r = client.post(
        "/api/data/import",
        files={"file": ("b.zip", bundle, "application/zip")},
        data={"replace": "true"},
    )
    assert r.status_code == 200
    body = r.json()
    assert client._paths["transactions"].read_text() == orig
    assert body["undo_checkpoint_id"]

    # The checkpoint is listed with kind="import".
    cps = ckpt.list_checkpoints()
    match = [c for c in cps if c["id"] == body["undo_checkpoint_id"]]
    assert match and match[0]["kind"] == "import"


def test_import_replace_is_undoable(client):
    """Restoring the pre-import checkpoint reverts the import."""
    bundle = client.get("/api/data/export").content
    pre_import = client._paths["transactions"].read_text()
    client._paths["transactions"].write_text(json.dumps([{"id": "pre-import-state"}]))
    state_before_import = client._paths["transactions"].read_text()

    r = client.post(
        "/api/data/import",
        files={"file": ("b.zip", bundle, "application/zip")},
        data={"replace": "true"},
    )
    cp_id = r.json()["undo_checkpoint_id"]
    assert client._paths["transactions"].read_text() == pre_import  # imported

    ckpt.restore(cp_id, mode="data")
    assert client._paths["transactions"].read_text() == state_before_import


def test_import_undo_round_trips_every_imported_file(client):
    """P2-PT1 FIX 1: every file/store the import overwrites is checkpoint-captured, so
    restoring the pre-import checkpoint round-trips ALL of them (not just one) —
    "transactions" (a plain PORTABLE_FILES entry, rides the generic per-file write-hook
    path) AND "profile" (DB-bridged, Wave 5 — undoable via its own fire_db_write_hook
    calls, see profile_io.import_profile / portable_bridge._wipe_profile) both covered.
    Previously runtime files in PORTABLE_FILES were checkpoint-excluded → silent,
    non-undoable loss."""
    bundle = client.get("/api/data/export").content

    # Mutate every portable file/store to a distinct pre-import state.
    client._paths["transactions"].write_text(json.dumps([{"id": "pre-import-txn"}]))
    ProfileStore().set_raw({"name": "pre-import-profile"})
    pre = {
        "transactions": client._paths["transactions"].read_text(),
        "profile": ProfileStore().get_raw(),
    }

    r = client.post(
        "/api/data/import",
        files={"file": ("b.zip", bundle, "application/zip")},
        data={"replace": "true"},
    )
    cp_id = r.json()["undo_checkpoint_id"]
    # Import replaced both (they differ from the pre-import state).
    assert client._paths["transactions"].read_text() != pre["transactions"]
    assert ProfileStore().get_raw() != pre["profile"]

    ckpt.restore(cp_id, mode="data")

    # The pre-import state of EVERY imported file/store is restored.
    assert client._paths["transactions"].read_text() == pre["transactions"]
    assert ProfileStore().get_raw() == pre["profile"]


def test_import_upload_larger_than_free_disk_413(client, monkeypatch):
    monkeypatch.setattr(api.shutil, "disk_usage", lambda _p: type("U", (), {"free": 1024})())
    r = client.post(
        "/api/data/import",
        files={"file": ("b.zip", b"x" * 64, "application/zip")},
        data={"replace": "true"},
    )
    assert r.status_code == 413


def test_import_malformed_bundle_422(client):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        sha = __import__("hashlib").sha256(b"{bad").hexdigest()
        zf.writestr("profile.json", b"{bad")
        zf.writestr(
            "manifest.json",
            json.dumps(
                {"schema_version": svc.SCHEMA_VERSION, "entries": {"profile.json": {"sha256": sha}}}
            ),
        )
    r = client.post(
        "/api/data/import",
        files={"file": ("b.zip", buf.getvalue(), "application/zip")},
        data={"replace": "true"},
    )
    assert r.status_code == 422


def test_import_replace_base_exception_still_unpins_turn(client, monkeypatch):
    """Routed fix (Plan 3 task 1): the begin_turn/end_turn pairing around
    apply_import used to be a pair of `except OpenNetWorthError` / `except
    Exception` blocks, not a true try/finally — a BaseException that is NOT an
    Exception (e.g. asyncio.CancelledError, the real shape an async client
    disconnect produces) matched neither clause, so end_turn never ran: the turn
    stayed counted in _active_turns forever, and every import attempt after would
    have hit the "an import is already in progress" 409 guard.

    Reproduced with a bare BaseException subclass (deliberately NOT an Exception)
    rather than asyncio.CancelledError itself, so the assertion is about the
    try/finally SHAPE, not about asyncio's own cancellation semantics or how the
    ASGI stack happens to propagate a cancellation. Calls the endpoint function
    directly (bypassing TestClient/ASGI) so the injected BaseException is exactly
    what propagates, with no framework layer in between able to swallow or
    reshape it."""
    import asyncio
    import io

    import pytest
    from fastapi import UploadFile

    class _NotAnException(BaseException):
        """A BaseException that is NOT an Exception — proves the fix is a real
        try/finally, not merely a wider `except` clause."""

    bundle = client.get("/api/data/export").content
    original_apply_import = svc.apply_import

    def _boom(_zip_bytes):
        raise _NotAnException("simulated non-Exception failure mid-import")

    monkeypatch.setattr(svc, "apply_import", _boom)
    upload = UploadFile(io.BytesIO(bundle), filename="b.zip")

    with pytest.raises(_NotAnException):
        asyncio.run(api.import_data(file=upload, replace=True))

    assert ckpt._session_turn_active("import") is False, (
        "end_turn must run on a BaseException path too, or the "
        "'import already in progress' guard stays tripped forever"
    )

    # Behavioral proof: a real second import isn't refused by a leaked guard.
    monkeypatch.setattr(svc, "apply_import", original_apply_import)
    r = client.post(
        "/api/data/import",
        files={"file": ("b.zip", bundle, "application/zip")},
        data={"replace": "true"},
    )
    assert r.status_code == 200


def test_reset_uses_shared_manifest(client):
    """/api/reset still works after refactoring onto core.portable (the shared
    file list). Resets each portable file to its empty state, and the DB-bridged
    profile store to its blank _RESET_PROFILE shape."""
    client._paths["transactions"].write_text(json.dumps([{"id": "x"}]))
    r = client.post("/api/reset")
    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert json.loads(client._paths["transactions"].read_text()) == []
    # Profile reset to the blank shape.
    assert ProfileStore().get_raw()["name"] == ""


def test_reset_reopens_first_run(client):
    """A completed instance is not fresh; after /api/reset it must be, so the wizard shows again
    (the stamp used to survive the wipe and strand the user in a blank app)."""
    from backend.db.onboarding_state_store import OnboardingStateStore

    OnboardingStateStore().mark_complete()
    assert client.get("/api/setup-status").json()["is_fresh"] is False

    assert client.post("/api/reset").status_code == 200

    body = client.get("/api/setup-status").json()
    assert body["is_fresh"] is True
    assert body["onboarding_completed_at"] is None
    assert body["restore_pending"] is False


def test_reset_gives_the_book_a_new_id(client):
    """A browser tab left open across a reset compares this id to the one it cached under —
    same id after a wipe would keep showing the old book's conversations and screens."""
    before = client.get("/api/setup-status").json()["book_id"]
    assert client.get("/api/setup-status").json()["book_id"] == before

    assert client.post("/api/reset").status_code == 200

    after = client.get("/api/setup-status").json()["book_id"]
    assert after and after != before
    assert client.get("/api/setup-status").json()["book_id"] == after


def test_import_replace_gives_the_book_a_new_id(client):
    bundle = client.get("/api/data/export").content
    before = client.get("/api/setup-status").json()["book_id"]

    r = client.post(
        "/api/data/import",
        files={"file": ("b.zip", bundle, "application/zip")},
        data={"replace": "true"},
    )
    assert r.status_code == 200

    after = client.get("/api/setup-status").json()["book_id"]
    assert after and after != before


def test_import_handlers_run_in_the_threadpool_not_on_the_event_loop():
    import inspect

    for handler in (api.import_validate, api.import_data, api.export_data):
        assert not inspect.iscoroutinefunction(handler)


def test_the_app_keeps_answering_while_an_import_is_running(client, monkeypatch):
    import threading

    entered, release = threading.Event(), threading.Event()

    def slow_import(_bundle):
        entered.set()
        assert release.wait(10)
        return {"restored_files": [], "row_counts": {}, "blob_files": 0}

    monkeypatch.setattr(api.portability, "apply_import", slow_import)
    done: dict = {}

    def run_import():
        done["r"] = client.post(
            "/api/data/import",
            files={"file": ("b.zip", b"PK\x05\x06" + b"\x00" * 18, "application/zip")},
            data={"replace": "true"},
        )

    thread = threading.Thread(target=run_import)
    thread.start()
    try:
        assert entered.wait(10)
        # The event loop is free: a second request is served while the import is mid-flight.
        assert client.get("/api/portability-probe-missing").status_code == 404
    finally:
        release.set()
        thread.join(10)
    assert done["r"].status_code == 200


def test_reset_returns_starter_skills_to_their_shipped_state(client, tmp_path, monkeypatch):
    """/api/reset wipes skills_state but not the skill folders; a folder with no state row used
    to read as a DISABLED USER skill, so redoing first run 422'd on the onboarding handoff."""
    from backend.db import skill_store
    from backend.db.skill_store import SkillStore
    from backend.services.slash_command import resolve_slash_skills

    # The session-wide skills folder outlives each test's database; a private one keeps
    # "folder present, state row absent" caused by the reset alone.
    monkeypatch.setattr(skill_store, "SKILLS_DIR", tmp_path / "skills")
    (tmp_path / "skills").mkdir()
    store = SkillStore()
    store.seed_starters()
    assert store.find_by_id("onboarding-new-user")["enabled"] is True

    assert client.post("/api/reset").status_code == 200

    skill = store.find_by_id("onboarding-new-user")
    assert skill["author"] == "builtin"
    assert skill["enabled"] is True
    resolution = resolve_slash_skills("", ["onboarding-new-user"])
    assert resolution is not None and resolution.skill_ids == ("onboarding-new-user",)
