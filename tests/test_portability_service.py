"""P2-PT1 — export/import service: round-trip, redaction, adversarial bundles.

Fully isolated on a tmp DATA_DIR (project rule: never read/write live data). We
point the canonical ``core.portable`` manifest + ``services.portability`` session
dir at tmp files, so every read/write the service does stays in the sandbox.
"""

from __future__ import annotations

import io
import json
import zipfile

import pytest

from backend.core import portable
from backend.core.errors import ValidationError
from backend.services import portability as svc


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    data = tmp_path / "data"
    sessions = data / "sessions"
    sessions.mkdir(parents=True)

    profile = data / "profile.json"
    # Three plain, GENERIC flat-list stand-in stores — this file tests the flat-file
    # replace/redaction/checkpoint-hook mechanism itself, not anything account- or
    # transaction-specific, so any never-DB-backed PORTABLE_FILES names work.
    # Deliberately NOT "accounts.json"/"transactions.json" (plan 3 task 6), NOT
    # "rules.json"/"alerts.json" (plan 4a task 7, an EARLIER swap for the identical
    # reason — "rules.json" survived, "alerts.json" did not), NOT "alerts.json"/
    # "memory.json" (plan 4d task 6, a second swap for the same collision), and NOT
    # "actions.json" (Wave 5C, JSON->DB — a THIRD collision: this pair's own prior
    # choice for the second stand-in slot joined ``portable_bridge.BRIDGED`` too,
    # confirmed via ``action_store.py``'s own docstring — swapped here to
    # "custom_widgets.json"): every one of those six
    # filenames is now DB-bridged — a Path merely NAMED that way, monkeypatched into a
    # narrowed PORTABLE_FILES, would get routed through services.portability's
    # DB-bridge step and read/write a table instead of this sandbox's plain files,
    # breaking the isolation this whole fixture exists to guarantee. "rules.json"/
    # "custom_widgets.json"/"scheduled_tasks.json" are plain list-rooted JsonStore
    # files with no migration in flight (verified against the live BRIDGED registry
    # — backend/db/portable_bridge.py's BRIDGED dict — not guessed).
    store_a = data / "rules.json"
    store_b = data / "custom_widgets.json"
    store_c = data / "scheduled_tasks.json"
    email = data / "email_ingest.json"

    # profile.json is ITSELF a real portable_bridge.BRIDGED entry now (Wave 5, JSON->DB —
    # not a stand-in collision like store_b above; this file's own bundle entry is the
    # REAL profile bridge, deliberately exercised since _base_currency() reads it for the
    # manifest). export_bytes_for/apply_import route it through the DB, never this path's
    # bytes — seed/verify through ProfileStore, not the file, below and at every call site
    # that touches "profile" in this file.
    from backend.db.profile_store import ProfileStore

    ProfileStore().set_raw({"name": "Alex", "base_currency": "SGD"})
    store_a.write_text(json.dumps([{"id": "a1", "name": "Bank"}]))
    store_b.write_text(json.dumps([{"id": "t1"}, {"id": "t2"}]))
    store_c.write_text(json.dumps([{"id": "m1", "fact": "likes index funds"}]))
    email.write_text(
        json.dumps(
            {
                "enabled": True,
                "imap_host": "imap.x.com",
                "imap_user": "u",
                "imap_password": "SECRET",
            }
        )
    )

    # Narrow the canonical manifest to just these files for the test. email is
    # written on disk but DELIBERATELY left out of PORTABLE_FILES, mirroring the
    # production exclusion (P2-PT1 FIX 1) — it is runtime/transient state.
    monkeypatch.setattr(portable, "PORTABLE_FILES", [profile, store_a, store_b, store_c])
    monkeypatch.setattr(portable, "PORTABLE_DIRS", [sessions])
    monkeypatch.setattr(svc, "_ALLOWED_FILENAMES", {p.name for p in portable.PORTABLE_FILES})
    # _base_currency() reads services.profile.read_profile_raw(), which is DB-backed now
    # (ProfileStore, seeded above) — no PROFILE_JSON path to isolate any more; conftest's
    # autouse Tier-2 table clear already keeps this test's profile row from leaking.

    # A chat session — sessions is DB-backed now (Wave 5E, JSON->DB; services.portability
    # no longer has a SESSIONS_DIR to monkeypatch) — seed directly through SessionStore,
    # which build_export_bundle's session_bundle_entries() reads via the SAME shared/
    # default engine (never cached, resolved fresh per call, same as every other
    # DB-backed store in this migration).
    from backend.db.session_store import SessionStore

    SessionStore().set_transcript("sess-abc", [{"role": "user"}])

    return {
        "data": data,
        "sessions": sessions,
        "profile": profile,
        "store_a": store_a,
        "store_b": store_b,
        "store_c": store_c,
        "email": email,
    }


def _open(bundle: bytes) -> zipfile.ZipFile:
    return zipfile.ZipFile(io.BytesIO(bundle))


def test_export_bundle_contains_files_and_manifest(sandbox):
    bundle = svc.build_export_bundle()
    with _open(bundle) as zf:
        names = set(zf.namelist())
        assert "manifest.json" in names
        assert "profile.json" in names
        assert "rules.json" in names
        assert "custom_widgets.json" in names
        assert "scheduled_tasks.json" in names
        assert "sessions/sess-abc.json" in names
        manifest = json.loads(zf.read("manifest.json"))

    assert manifest["schema_version"] == svc.SCHEMA_VERSION
    assert manifest["base_currency"] == "SGD"
    assert manifest["entries"]["custom_widgets.json"]["row_count"] == 2


def test_email_ingest_not_portable(sandbox):
    # P2-PT1 FIX 1: the email-ingest config / poll cursor is runtime/transient
    # state (checkpoint-excluded), so it must NOT appear in the export bundle —
    # which also means no IMAP credential can ever travel in a bundle.
    bundle = svc.build_export_bundle()
    with _open(bundle) as zf:
        names = set(zf.namelist())
    assert "email_ingest.json" not in names


def test_export_excludes_caches_and_runtime(sandbox, tmp_path):
    # fx_cache / cost_log / connector_sessions are not in PORTABLE_FILES, so they
    # can never appear in a bundle regardless of presence on disk.
    bundle = svc.build_export_bundle()
    with _open(bundle) as zf:
        names = set(zf.namelist())
    for excluded in ("fx_cache.json", "cost_log.json", "connector_sessions.json"):
        assert excluded not in names


def test_inspect_reports_files_and_sha_ok(sandbox):
    report = svc.inspect_bundle(svc.build_export_bundle())
    assert report["ok"] is True
    assert report["errors"] == []
    by_name = {f["name"]: f for f in report["files"]}
    assert by_name["custom_widgets.json"]["row_count"] == 2
    assert all(f["sha_ok"] for f in report["files"])


def test_inspect_writes_nothing(sandbox):
    bundle = svc.build_export_bundle()
    before = sandbox["store_b"].read_text()
    sandbox["store_b"].write_text(json.dumps([{"id": "mutated"}]))
    svc.inspect_bundle(bundle)
    # inspect must not restore — the mutation stands.
    assert sandbox["store_b"].read_text() != before
    assert json.loads(sandbox["store_b"].read_text()) == [{"id": "mutated"}]


def test_round_trip_restores_byte_identical(sandbox):
    from backend.db.profile_store import ProfileStore

    bundle = svc.build_export_bundle()
    orig_b = sandbox["store_b"].read_text()
    orig_profile = ProfileStore().get_raw()

    # Mutate live data — profile.json is DB-bridged (profile is a real ProfileStore row,
    # not a plain file the import writes bytes into).
    sandbox["store_b"].write_text(json.dumps([{"id": "wrong"}]))
    ProfileStore().set_raw({"name": "Hacker"})

    result = svc.apply_import(bundle)

    assert "custom_widgets.json" in result["restored_files"]
    assert sandbox["store_b"].read_text() == orig_b
    assert ProfileStore().get_raw() == orig_profile
    assert result["row_counts"]["custom_widgets.json"] == 2


def test_absent_file_reset_to_empty_on_import(sandbox):
    # Build a bundle WITHOUT store_a (delete it before export).
    sandbox["store_a"].unlink()
    bundle = svc.build_export_bundle()
    # Now put data back into store_a; import should reset it to empty.
    sandbox["store_a"].write_text(json.dumps([{"id": "leftover"}]))

    svc.apply_import(bundle)
    assert json.loads(sandbox["store_a"].read_text()) == []


def test_import_replaces_sessions(sandbox):
    from backend.db.session_store import SessionStore

    bundle = svc.build_export_bundle()
    # Add a stray session that is NOT in the bundle. sessions is DB-backed now
    # (Wave 5E, JSON->DB) — seed directly through SessionStore instead of a file.
    SessionStore().set_transcript("stray", [])
    svc.apply_import(bundle)
    ids = {row["id"] for row in SessionStore().all_meta()}
    assert ids == {"sess-abc"}  # stray removed, bundle session present


# ── Adversarial ──────────────────────────────────────────────────────────────


def test_malformed_json_entry_rejected(sandbox):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        bad = b"{not json"
        sha = __import__("hashlib").sha256(bad).hexdigest()
        zf.writestr("profile.json", bad)
        zf.writestr(
            "manifest.json",
            json.dumps(
                {
                    "schema_version": svc.SCHEMA_VERSION,
                    "entries": {"profile.json": {"sha256": sha}},
                }
            ),
        )
    report = svc.inspect_bundle(buf.getvalue())
    assert report["ok"] is False
    assert any("not valid JSON" in e for e in report["errors"])
    with pytest.raises(ValidationError):
        svc.apply_import(buf.getvalue())


def test_zip_slip_entry_rejected(sandbox):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("../../etc/evil.json", b"[]")
        zf.writestr(
            "manifest.json", json.dumps({"schema_version": svc.SCHEMA_VERSION, "entries": {}})
        )
    report = svc.inspect_bundle(buf.getvalue())
    assert report["ok"] is False
    assert any("unsafe" in e or "unknown" in e for e in report["errors"])


def test_wrong_schema_version_refused(sandbox):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("manifest.json", json.dumps({"schema_version": 999, "entries": {}}))
    report = svc.inspect_bundle(buf.getvalue())
    assert report["ok"] is False
    assert any("schema_version" in e for e in report["errors"])


def test_v1_bundle_refused_with_the_seven_type_upgrade_path(sandbox):
    """A v1 bundle's `accounts.json` carries the 26 pre-2026-09-07 account types. Restoring one
    puts rows in the book that `band_of` cannot classify and net worth then 500s. The refusal is
    the fix — there is no remap — and the message says what to do instead."""
    assert svc.SCHEMA_VERSION == 2
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("manifest.json", json.dumps({"schema_version": 1, "entries": {}}))
    report = svc.inspect_bundle(buf.getvalue())
    assert report["ok"] is False
    message = " ".join(report["errors"])
    assert "seven-type account schema" in message
    assert "export again" in message
    with pytest.raises(ValidationError):
        svc.apply_import(buf.getvalue())


def test_accounts_bundle_with_a_legacy_type_fails_shape_validation(sandbox):
    """`accounts.json` is the one bundle entry whose rows must be classifiable by THIS build. A
    row typed `savings` (or any other retired type, or none at all) is refused at inspect time,
    before `apply_money_bundle` ever writes it."""
    from backend.db import money_io

    errors = money_io.validate_money_file_shape(
        "accounts.json",
        [{"id": "a1", "type": "cash"}, {"id": "a2", "type": "savings"}, {"id": "a3"}],
    )
    assert len(errors) == 2
    assert "'savings'" in errors[0] and "a2" in errors[0]
    assert "a3" in errors[1]
    assert money_io.validate_money_file_shape("accounts.json", [{"id": "a", "type": "loan"}]) == []


def test_tampered_sha_flagged(sandbox):
    bundle = svc.build_export_bundle()
    # Re-zip with a tampered profile but the original manifest sha.
    with _open(bundle) as zf:
        manifest = zf.read("manifest.json")
        names = zf.namelist()
        contents = {n: zf.read(n) for n in names}
    contents["profile.json"] = json.dumps({"name": "TAMPERED"}).encode()
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as zf:
        for n, data in contents.items():
            zf.writestr(n, manifest if n == "manifest.json" else data)
    report = svc.inspect_bundle(out.getvalue())
    assert report["ok"] is False
    assert any("sha256 mismatch" in e for e in report["errors"])


def test_not_a_zip_rejected(sandbox):
    report = svc.inspect_bundle(b"this is not a zip")
    assert report["ok"] is False
    assert any("zip" in e for e in report["errors"])


# ── BUG-HUNT REGRESSION: session import checkpoint coverage ──────────────────


def test_session_import_fires_write_hook(sandbox, monkeypatch):
    """_replace_sessions must call fire_db_write_hook for each existing session row
    before wipe_all_sessions deletes it, so the active pre-import checkpoint can
    snapshot existing chat history (making import undoable in principle — sessions
    itself is checkpoint-EXCLUDED, so the capture is a structural no-op, but the hook
    must still fire unconditionally like every other DB-backed store's write path).

    Regression: previously sessions were written via atomic_write_text with NO hook
    call, so a crash mid-write would lose chat history with no undo path. sessions is
    DB-backed now (Wave 5E, JSON->DB) — the write hook is fire_db_write_hook (keyed by
    table name + row id), not the file-based fire_write_hook; db/session_io.py imports
    the name directly (``from backend.core.io import fire_db_write_hook``), so THAT
    local binding is the one that must be patched, not core.io's own.
    """
    fired_calls: list[tuple[str, list]] = []

    def capturing_hook(table_name, rows, conn=None) -> None:
        fired_calls.append((table_name, list(rows)))

    monkeypatch.setattr("backend.db.session_io.fire_db_write_hook", capturing_hook)

    bundle = svc.build_export_bundle()
    svc.apply_import(bundle)

    # The existing session row (sess-abc) must have been hooked before wipe_all_sessions
    # deleted it.
    assert any(
        table == "sessions" and any(row_id == "sess-abc" for row_id, _before in rows)
        for table, rows in fired_calls
    ), (
        "fire_db_write_hook was not called for the existing session sess-abc — "
        "session import is not checkpoint-covered"
    )


def test_new_session_from_bundle_fires_write_hook(sandbox, monkeypatch):
    """A session arriving fresh from the bundle (no pre-existing row) must also be
    hooked, via import_session_row's own unconditional fire at INSERT time — distinct
    from wipe_all_sessions's DELETE-time firing the sibling test above pins."""
    from backend.db.session_store import SessionStore

    # Bundle contains sess-abc (seeded in fixture).
    bundle = svc.build_export_bundle()

    # Remove the existing row so only the bundle-driven INSERT fires for it (mirrors
    # the old "clear existing session files so only the new bundle write fires").
    SessionStore().delete("sess-abc")

    fired_calls: list[tuple[str, list]] = []

    def capturing_hook(table_name, rows, conn=None) -> None:
        fired_calls.append((table_name, list(rows)))

    monkeypatch.setattr("backend.db.session_io.fire_db_write_hook", capturing_hook)

    svc.apply_import(bundle)

    # The new bundle session write must have fired the hook.
    assert any(
        table == "sessions" and any(row_id == "sess-abc" for row_id, _before in rows)
        for table, rows in fired_calls
    ), "fire_db_write_hook not called for the bundle-driven session insert — not checkpoint-covered"


# ── P2-PT1 FIX 1: dropped runtime/transient files ─────────────────────────────


def test_runtime_files_dropped_from_manifest():
    # The four checkpoint-excluded runtime/transient stores must not be portable
    # (using the REAL manifest, not the narrowed sandbox one).
    names = {p.name for p in portable.PORTABLE_FILES}
    for dropped in (
        "signals.json",
        "email_ingest.json",
        "pending_transactions.json",
        "corrections.json",
    ):
        assert dropped not in names


# ── P2-PT1 FIX 2: zip-bomb / unbounded decompress guard ───────────────────────


def _bomb_bundle(declared_size: int) -> bytes:
    """A bundle with a tiny actual payload but a forged (huge) declared
    decompressed ``file_size`` in the central directory — a classic high-ratio
    zip bomb. ``writestr`` recomputes file_size from the data, so we overwrite it
    on the stored ZipInfo *before* close writes the central directory."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(
            "manifest.json", json.dumps({"schema_version": svc.SCHEMA_VERSION, "entries": {}})
        )
        zf.writestr("profile.json", b"[]")  # tiny actual payload
        info = zf.getinfo("profile.json")
        info.file_size = declared_size  # forge the declared decompressed size
    return buf.getvalue()


def test_zip_bomb_declared_total_rejected(sandbox):
    # A bundle whose declared (uncompressed) total exceeds the cap is refused in
    # inspect (and apply) BEFORE any entry is read/written — the guard trusts the
    # declared size, so it rejects without inflating anything.
    bundle = _bomb_bundle(svc._MAX_TOTAL_UNCOMPRESSED + 1)

    report = svc.inspect_bundle(bundle)
    assert report["ok"] is False
    assert any("too large" in e for e in report["errors"])

    # And apply refuses without writing. profile.json is DB-bridged (a real ProfileStore
    # row), not a plain file the import writes bytes into.
    from backend.db.profile_store import ProfileStore

    before = ProfileStore().get_raw()
    with pytest.raises(ValidationError):
        svc.apply_import(bundle)
    assert ProfileStore().get_raw() == before


def test_zip_bomb_per_entry_cap(sandbox):
    bundle = _bomb_bundle(svc._MAX_ENTRY_UNCOMPRESSED + 1)
    report = svc.inspect_bundle(bundle)
    assert report["ok"] is False
    assert any("too large" in e for e in report["errors"])
