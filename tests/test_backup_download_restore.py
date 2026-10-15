"""services/backup.py — download resolution, restore, and auto/manual provenance.

Same isolation contract as test_backup_service.py: DATA_DIR/BACKUPS_DIR are monkeypatched
into tmp_path; AppConfigStore is DB-backed (runtime_state table) so db_isolated covers it —
these never touch live data.
"""

from __future__ import annotations

import zipfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from backend.services import backup as backup_service


@pytest.fixture
def isolated(tmp_path, monkeypatch, db_isolated):
    data_dir = tmp_path / "data"
    backups_dir = data_dir / "backups"
    data_dir.mkdir()
    backups_dir.mkdir()
    (data_dir / "accounts.json").write_text("[]")
    (data_dir / "transactions.json").write_text("[]")

    auth = data_dir / "auth.json"
    auth.write_text('{"password_hash": "current", "api_token": "onw_REAL"}')
    (data_dir / "plaid_config.json").write_text('{"secret": "plaid-secret"}')
    (data_dir / "tool_grants.json").write_text('[{"tool": "send_email"}]')
    (data_dir / "txn_audit.json").write_text('[{"op": "create"}]')

    monkeypatch.setattr(backup_service, "DATA_DIR", data_dir)
    monkeypatch.setattr(backup_service, "BACKUPS_DIR", backups_dir)
    monkeypatch.setattr(backup_service, "_EXCLUDE_DIR_NAMES", {"backups", "checkpoints"})

    # core.sensitive resolves against the REAL DATA_DIR; point its normalised sets at the
    # tmp tree so `is_secret`/`is_non_restorable` answer for these fixtures.
    from backend.core import sensitive

    def _norm(pth):
        return str(Path(pth).resolve()).casefold()

    secret_names = {"auth.json", "plaid_config.json"}
    non_restorable_names = secret_names | {"tool_grants.json", "txn_audit.json"}
    monkeypatch.setattr(sensitive, "_SECRET_NORM", {_norm(data_dir / n) for n in secret_names})
    monkeypatch.setattr(
        sensitive, "_NON_RESTORABLE_NORM", {_norm(data_dir / n) for n in non_restorable_names}
    )

    # AppConfigStore is DB-backed now (runtime_state table) — db_isolated gives it a
    # fresh, isolated sqlite file; no more per-store path to monkeypatch.
    return data_dir, backups_dir, auth


AT = datetime(2026, 7, 6, 3, 30, tzinfo=timezone.utc)


# ── provenance ────────────────────────────────────────────────────────────────


def test_scheduled_run_is_listed_as_automatic(isolated):
    backup_service.run_backup(now=AT)
    (row,) = backup_service.list_backups()
    assert row["trigger"] == "auto"
    assert row["name"] == "20260706T033000Z"


def test_manual_run_is_listed_as_manual(isolated):
    backup_service.run_backup(now=AT, force=True, trigger="manual")
    (row,) = backup_service.list_backups()
    assert row["trigger"] == "manual"
    assert row["name"].endswith("-manual")


def test_manual_and_auto_runs_at_the_same_instant_do_not_collide(isolated):
    assert backup_service.run_backup(now=AT)["ok"] is True
    assert backup_service.run_backup(now=AT, force=True, trigger="manual")["ok"] is True
    assert {r["trigger"] for r in backup_service.list_backups()} == {"auto", "manual"}


# ── resolve (the only caller-supplied path this module opens) ─────────────────


def test_resolve_backup_finds_a_real_backup(isolated):
    name = backup_service.run_backup(now=AT)["name"]
    assert backup_service.resolve_backup(name) is not None


@pytest.mark.parametrize(
    "name",
    [
        "../../../etc/passwd",
        "..%2F..%2Fetc",
        "/etc/passwd",
        "not-a-timestamp",
        "",
        "20260706T033000Z/../../secrets",
    ],
)
def test_resolve_backup_refuses_anything_that_is_not_a_backup_name(isolated, name):
    """A name has to parse as a literal backup timestamp, so traversal never reaches disk."""
    assert backup_service.resolve_backup(name) is None


def test_resolve_backup_returns_none_for_an_unknown_but_well_formed_name(isolated):
    assert backup_service.resolve_backup("20200101T000000Z") is None


# ── restore ───────────────────────────────────────────────────────────────────


def test_restore_puts_back_the_snapshot_contents(isolated):
    data_dir, _, _ = isolated
    name = backup_service.run_backup(now=AT)["name"]

    (data_dir / "accounts.json").write_text('[{"id": "changed"}]')
    result = backup_service.restore_backup(name)

    assert result["ok"] is True
    assert (data_dir / "accounts.json").read_text() == "[]"


def test_restore_gives_the_book_a_new_id(isolated):
    """A tab open on the book this restore replaced compares ids to know its caches are stale."""
    from backend.db.onboarding_state_store import OnboardingStateStore

    name = backup_service.run_backup(now=AT)["name"]
    before = OnboardingStateStore().book_id()

    assert backup_service.restore_backup(name)["ok"] is True

    assert OnboardingStateStore().book_id() != before


def test_restore_deletes_files_added_after_the_snapshot(isolated):
    """A point-in-time restore, not a merge — the result IS the snapshot."""
    data_dir, _, _ = isolated
    name = backup_service.run_backup(now=AT)["name"]

    (data_dir / "positions.json").write_text("[]")
    backup_service.restore_backup(name)

    assert not (data_dir / "positions.json").exists()


def test_restore_never_rolls_back_credentials(isolated):
    """auth.json is not "your data": restoring it would silently revert a password change
    and can lock the operator out, with no reset email to recover through."""
    data_dir, _, auth = isolated
    name = backup_service.run_backup(now=AT)["name"]

    auth.write_text('{"password_hash": "rotated-after-the-backup"}')
    backup_service.restore_backup(name)

    assert auth.read_text() == '{"password_hash": "rotated-after-the-backup"}'


def test_restore_writes_a_safety_backup_first(isolated):
    """The UI promises a restore can itself be undone — this is what makes that true."""
    data_dir, _, _ = isolated
    name = backup_service.run_backup(now=AT)["name"]
    (data_dir / "accounts.json").write_text('[{"id": "about-to-be-replaced"}]')

    result = backup_service.restore_backup(name)

    safety = backup_service.resolve_backup(result["safety_backup"])
    assert safety is not None
    with zipfile.ZipFile(safety) as zf:
        assert zf.read("accounts.json") == b'[{"id": "about-to-be-replaced"}]'


def test_restore_of_an_unknown_backup_is_refused_and_changes_nothing(isolated):
    data_dir, backups_dir, _ = isolated
    (data_dir / "accounts.json").write_text('[{"id": "untouched"}]')

    result = backup_service.restore_backup("20200101T000000Z")

    assert result["ok"] is False
    assert result["error"] == "unknown backup"
    assert (data_dir / "accounts.json").read_text() == '[{"id": "untouched"}]'
    # A refused restore must not leave a safety backup behind either.
    assert backup_service.list_backups() == []


def test_restore_refuses_an_archive_that_escapes_the_data_dir(isolated, tmp_path):
    """Zip-slip: a hand-crafted archive naming '../' must be refused, not extracted."""
    data_dir, backups_dir, _ = isolated
    evil = backups_dir / "20260706T033000Z.zip"
    with zipfile.ZipFile(evil, "w") as zf:
        zf.writestr("../escaped.json", "pwned")

    result = backup_service.restore_backup("20260706T033000Z")

    assert result["ok"] is False
    assert "unsafe path" in result["error"]
    assert not (data_dir.parent / "escaped.json").exists()


# ── Download is sanitised ─────────────────────────────────────────────────────


def test_download_copy_strips_every_credential_file(isolated, tmp_path):
    """A DR archive on disk holds auth.json and every connector secret — that is what makes
    it a DR archive. Handing THAT to an HTTP caller would turn one authenticated GET into the
    session-signing key, the API token and every bank credential on the box."""
    data_dir, _, _ = isolated
    name = backup_service.run_backup(now=AT)["name"]

    out = tmp_path / "download.zip"
    result = backup_service.write_sanitised_copy(name, out)

    assert result["ok"] is True
    with zipfile.ZipFile(out) as zf:
        names = set(zf.namelist())
    assert "auth.json" not in names
    assert "plaid_config.json" not in names
    assert result["stripped"] == ["auth.json", "plaid_config.json"]


def test_download_copy_keeps_the_user_data(isolated, tmp_path):
    """Stripping secrets must not strip the thing the user actually wanted."""
    data_dir, _, _ = isolated
    name = backup_service.run_backup(now=AT)["name"]

    out = tmp_path / "download.zip"
    backup_service.write_sanitised_copy(name, out)

    with zipfile.ZipFile(out) as zf:
        assert "accounts.json" in zf.namelist()
        assert zf.read("transactions.json") == b"[]"


def test_download_copy_strips_a_hand_made_copy_of_a_secret_file(isolated, tmp_path):
    """A hand-made safety copy of a secret file (e.g. an operator's own
    ``auth.json.pre-reset-20260723-185559`` made before editing the file by hand) carries the
    SAME plaintext as the original and must be stripped from a sanitised download too — not
    just the exact original filename (security-review regression).

    ``auth.json.old`` is deliberately NOT one of the legacy-migration-trail marker suffixes
    (``.migrated-``/``.removed-``/``.pre-``/``.corrupt-``/``.bak``) the nightly backup walk
    itself now filters (Part 1a) — so it reaches ``write_sanitised_copy`` unfiltered, and this
    proves `core.sensitive.is_secret`'s OWN generalized match strips it, independent of that
    separate filter. ``auth.json.pre-reset-20260723-185559`` (the exact shape from the real
    incident) is also asserted absent, belt-and-suspenders with the trail-file filter."""
    data_dir, _, auth = isolated
    (data_dir / "auth.json.old").write_text(auth.read_text())
    (data_dir / "auth.json.pre-reset-20260723-185559").write_text(auth.read_text())

    name = backup_service.run_backup(now=AT)["name"]
    out = tmp_path / "download.zip"
    result = backup_service.write_sanitised_copy(name, out)

    assert result["ok"] is True
    with zipfile.ZipFile(out) as zf:
        names = set(zf.namelist())
    assert "auth.json.old" not in names
    assert "auth.json.pre-reset-20260723-185559" not in names
    assert "auth.json.old" in result["stripped"]


def test_download_copy_of_an_unknown_backup_is_refused(isolated, tmp_path):
    result = backup_service.write_sanitised_copy("20200101T000000Z", tmp_path / "x.zip")
    assert result["ok"] is False
    assert result["error"] == "unknown backup"


# ── Restore protects more than credentials ────────────────────────────────────


def test_restore_never_rolls_back_the_tool_grant_table(isolated):
    """Revoke a grant -> restore a backup from before the revocation -> the grant is back.
    That is privilege escalation through an unrelated feature; checkpoints exclude the grant
    table for exactly this reason and a REST restore must not be the way around it."""
    data_dir, _, _ = isolated
    name = backup_service.run_backup(now=AT)["name"]

    (data_dir / "tool_grants.json").write_text("[]")  # the owner revoked everything
    backup_service.restore_backup(name)

    assert (data_dir / "tool_grants.json").read_text() == "[]"


def test_restore_never_erases_the_audit_trail(isolated):
    """A data restore reverts WHAT the data is, never the record of how it got there."""
    data_dir, _, _ = isolated
    name = backup_service.run_backup(now=AT)["name"]

    (data_dir / "txn_audit.json").write_text('[{"op": "create"}, {"op": "delete"}]')
    backup_service.restore_backup(name)

    assert "delete" in (data_dir / "txn_audit.json").read_text()


def test_restore_never_rolls_back_a_rotated_connector_secret(isolated):
    data_dir, _, _ = isolated
    name = backup_service.run_backup(now=AT)["name"]

    (data_dir / "plaid_config.json").write_text('{"secret": "rotated"}')
    backup_service.restore_backup(name)

    assert (data_dir / "plaid_config.json").read_text() == '{"secret": "rotated"}'


def test_restore_does_not_delete_a_protected_file_absent_from_the_archive(isolated):
    """The delete pass makes the result equal the snapshot — but not for files it is not
    allowed to restore, or a protected file created after the backup would be wiped."""
    data_dir, _, _ = isolated
    name = backup_service.run_backup(now=AT)["name"]

    (data_dir / "auth.json").unlink()
    backup_service.run_backup(now=AT.replace(hour=4))  # archive with no auth.json
    (data_dir / "auth.json").write_text('{"password_hash": "new"}')

    backup_service.restore_backup(name)
    assert (data_dir / "auth.json").read_text() == '{"password_hash": "new"}'


def test_restoring_the_oldest_backup_survives_the_safety_backup_prune(isolated):
    """The trick this pins: `restore_backup` opens the archive BEFORE writing its safety
    backup, because that write also PRUNES to the retention window — and the backup being
    restored is by definition an old one. Restoring the oldest backup you still have must not
    delete it out from under the call.

    (This is the same clock bug that had already time-bombed four tests in
    test_backup_service.py: prune read the real clock while run_backup stamped a supplied one.)
    """
    data_dir, _, _ = isolated
    cfg = backup_service.AppConfigStore()
    cfg.set_backup_retention_days(1)

    name = backup_service.run_backup(now=AT)["name"]
    (data_dir / "accounts.json").write_text('[{"id": "changed"}]')

    # "Now" is well past the retention window from AT, so the safety backup's prune step
    # would remove the archive we are mid-way through reading.
    later = AT.replace(year=AT.year + 1)
    monkey_now = backup_service.datetime

    class _FrozenNow(monkey_now):  # type: ignore[misc, valid-type]
        @classmethod
        def now(cls, tz=None):
            return later

    backup_service.datetime = _FrozenNow  # type: ignore[misc]
    try:
        result = backup_service.restore_backup(name)
    finally:
        backup_service.datetime = monkey_now  # type: ignore[misc]

    assert result["ok"] is True, result
    assert (data_dir / "accounts.json").read_text() == "[]"


def test_a_failed_restore_reports_the_safety_backup(isolated, monkeypatch):
    """The delete pass runs before the write pass, so a mid-restore failure leaves DATA_DIR
    mutated. The error must name the recovery point rather than leaving the user to guess
    one exists."""
    data_dir, _, _ = isolated
    name = backup_service.run_backup(now=AT)["name"]

    def _boom(*_a, **_k):
        raise OSError("no space left on device")

    monkeypatch.setattr(backup_service, "_atomic_write_bytes", _boom)
    result = backup_service.restore_backup(name)

    assert result["ok"] is False
    assert result["safety_backup"], "a failed restore must name its safety backup"
    assert backup_service.resolve_backup(result["safety_backup"]) is not None


# ── a backup from another version of the app is refused before anything changes ─────────────


@pytest.fixture
def db_in_data_dir(isolated, monkeypatch):
    """The live database lives INSIDE the data dir, so a backup carries ``onw.db`` and a restore
    would swap it. The file is a copy of the isolated test database (stamped at this code's head)."""
    import shutil

    from backend.db.engine import get_engine

    data_dir, _, _ = isolated
    live = data_dir / "onw.db"
    shutil.copyfile(get_engine().url.database, live)
    monkeypatch.setattr(backup_service, "_live_db_path", lambda: live.resolve())
    return live


def _stamp(db: Path, revision: str) -> None:
    import sqlite3

    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE alembic_version SET version_num = ?", (revision,))


def test_restore_refuses_a_backup_made_by_a_different_version(db_in_data_dir, isolated):
    data_dir, _, _ = isolated
    _stamp(db_in_data_dir, "202601010000_an_older_revision")
    name = backup_service.run_backup(now=AT)["name"]
    (data_dir / "accounts.json").write_text('[{"id": "changed"}]')

    result = backup_service.restore_backup(name)

    assert result["ok"] is False
    assert "different version of the app" in result["error"]
    assert "202601010000_an_older_revision" in result["error"]
    # Nothing destructive ran: no safety backup, and the live files are exactly as they were.
    assert "safety_backup" not in result
    assert [row["name"] for row in backup_service.list_backups()] == [name]
    assert (data_dir / "accounts.json").read_text() == '[{"id": "changed"}]'


def test_restore_refuses_a_backup_whose_database_carries_no_stamp(db_in_data_dir, isolated):
    import sqlite3

    with sqlite3.connect(db_in_data_dir) as conn:
        conn.execute("DELETE FROM alembic_version")
    name = backup_service.run_backup(now=AT)["name"]

    result = backup_service.restore_backup(name)

    assert result["ok"] is False
    assert "different version of the app" in result["error"]


def test_restore_accepts_a_backup_at_this_codes_head(db_in_data_dir, isolated):
    name = backup_service.run_backup(now=AT)["name"]

    result = backup_service.restore_backup(name)

    assert result["ok"] is True
