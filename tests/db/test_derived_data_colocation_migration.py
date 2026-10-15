"""Revision 0046 (``backend.db.alembic.versions.202608220859_derived_data_colocation``) — the
.claude/rfcs/2026-08-22-consolidate-derived-data-storage.md Option A migration: statements'
``statements_parsed/<id>.json`` sidecar moves onto ``files/statements/<id>.parsed.json`` (with
``statements_meta.parsed_rows_path`` replaced by a ``has_parsed_rows`` boolean), and
attachments' already-colocated ``<id>.json`` derived payload is renamed to
``<id>.parsed.json`` — the same naming rule for both stores, one directory each.

Runs the migration for REAL (``command.upgrade`` from ``"202608220022_files_dir_and_raw_blob_storage"`` to ``"202608220859_derived_data_colocation"``, on an
isolated tmp-path DB + tmp-path DATA_DIR — never the live tree) against hand-seeded
PRE-migration rows/files, mirroring ``tests/db/test_files_dir_migration.py``'s own shape.
"""

from __future__ import annotations

import json

import pytest
import sqlalchemy as sa
from sqlalchemy import create_engine, insert, select

from tests.db.conftest import patch_all_cutover_sources_missing

# A LOCAL, throwaway description of `statements_meta` AS IT ACTUALLY IS at the "202608220022_files_dir_and_raw_blob_storage"
# checkpoint — carrying `parsed_rows_path`, NOT `has_parsed_rows`. Mirrors revision 0008's
# own "LOCAL table descriptions, not backend.db.tables" reasoning: the LIVE
# `backend.db.tables.statements_meta` reflects TODAY's code (post-0046 — no
# `parsed_rows_path`, has `has_parsed_rows`), which does not match the real DB schema at the
# point in the chain these tests seed a row (BEFORE 0046 has run) — using it here would
# either raise "Unconsumed column names: parsed_rows_path" or silently insert into the wrong
# columns.
_statements_meta_pre46 = sa.table(
    "statements_meta",
    sa.column("id", sa.Text),
    sa.column("book_id", sa.Text),
    sa.column("seq", sa.Integer),
    sa.column("created_at", sa.Text),
    sa.column("filename", sa.Text),
    sa.column("mime", sa.Text),
    sa.column("size_bytes", sa.Integer),
    sa.column("file_hash", sa.Text),
    sa.column("file_path", sa.Text),
    sa.column("kind", sa.Text),
    sa.column("account_id", sa.Text),
    sa.column("parsed_at", sa.Text),
    sa.column("status", sa.Text),
    sa.column("meta_unavailable", sa.Integer),
    sa.column("import_meta", sa.JSON),
    sa.column("batch_ids", sa.JSON),
    sa.column("parsed_rows_path", sa.Text),
    sa.column("payload", sa.JSON),
)


# These walk the chain from an old revision themselves, so on Postgres (one shared database
# that earlier tests leave at head) they must start from an empty schema.
pytestmark = pytest.mark.usefixtures("scrubbed_db_url")


@pytest.fixture
def _isolated_files_dir(tmp_path, monkeypatch):
    import backend.core.paths as paths_mod

    data_dir = tmp_path / "onw_data"
    files_dir = data_dir / "files"
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(paths_mod, "DATA_DIR", data_dir)
    monkeypatch.setattr(paths_mod, "FILES_DIR", files_dir)
    # Revision 0046 resolves the NEW statements directory via
    # core.paths.statements_files_dir() — the ONE accessor every consumer calls (Fix 3,
    # security review round 2) — specifically because the suite's autouse
    # `_isolate_statement_files` fixture (tests/conftest.py) redirects THAT binding
    # independently of `core.paths.FILES_DIR` — keep this fixture's own override aligned or
    # the migration and this file's assertions look in different directories.
    monkeypatch.setattr(paths_mod, "STATEMENTS_FILES_DIR", files_dir / "statements")
    return data_dir, files_dir


def _upgrade(cfg, revision):
    from alembic import command

    command.upgrade(cfg, revision)


def _statement_row(**overrides) -> dict:
    row = {
        "id": "stmt_test000001",
        "book_id": "book_000000000000",
        "seq": 0,
        "created_at": "2026-08-01T00:00:00Z",
        "filename": "jan.pdf",
        "mime": "application/pdf",
        "size_bytes": 100,
        "file_hash": "deadbeef",
        "file_path": "files/statements/stmt_test000001.pdf",
        "kind": "pdf_pages",
        "account_id": None,
        "parsed_at": None,
        "status": None,
        "meta_unavailable": 0,
        "import_meta": None,
        "batch_ids": [],
        "parsed_rows_path": None,
        "payload": {
            "id": "stmt_test000001",
            "created_at": "2026-08-01T00:00:00Z",
            "filename": "jan.pdf",
            "mime": "application/pdf",
            "size_bytes": 100,
            "file_hash": "deadbeef",
            "file_path": "files/statements/stmt_test000001.pdf",
            "kind": "pdf_pages",
            "upload_entry": {},
            "parsed_at": None,
            "account_id": None,
            "batch_ids": [],
            "source_attachment_id": None,
        },
    }
    row.update(overrides)
    return row


def _seed_at_0045(db_url, monkeypatch, tmp_path, data_dir):
    from backend.db import engine as eng
    from backend.db.alembic_config import make_config

    monkeypatch.setenv("ONW_DATABASE_URL", db_url)
    patch_all_cutover_sources_missing(monkeypatch, tmp_path)
    cfg = make_config(db_url)
    eng.dispose_engine()
    _upgrade(cfg, "202608220022_files_dir_and_raw_blob_storage")
    return cfg


def test_migration_moves_statement_sidecar_and_sets_has_parsed_rows(
    db_url, monkeypatch, tmp_path, _isolated_files_dir
):
    from backend.db import engine as eng
    from backend.db import tables as t

    data_dir, files_dir = _isolated_files_dir
    cfg = _seed_at_0045(db_url, monkeypatch, tmp_path, data_dir)

    old_parsed_dir = data_dir / "statements_parsed"
    old_parsed_dir.mkdir(parents=True)
    parsed_rows = [
        {"date": "2026-01-05", "amount": 50.0, "direction": "debit", "description": "Groceries"}
    ]
    (old_parsed_dir / "stmt_test000001.json").write_text(json.dumps(parsed_rows))

    row = _statement_row(parsed_rows_path="statements_parsed/stmt_test000001.json")
    engine = create_engine(db_url)
    try:
        with engine.begin() as conn:
            conn.execute(insert(_statements_meta_pre46).values(**row))
    finally:
        engine.dispose()

    eng.dispose_engine()
    _upgrade(cfg, "202608220859_derived_data_colocation")
    eng.dispose_engine()

    new_sidecar = files_dir / "statements" / "stmt_test000001.parsed.json"
    assert new_sidecar.exists()
    assert json.loads(new_sidecar.read_text()) == parsed_rows

    # The old file is preserved as a trail, never deleted.
    trail = list(old_parsed_dir.glob("stmt_test000001.json.migrated-*"))
    assert len(trail) == 1
    assert json.loads(trail[0].read_text()) == parsed_rows

    # This revision's own file-move behavior is fully proven above — advance the rest of the
    # way to head before reading through the LIVE `tables.py`/`StatementStore`, which describe
    # TODAY's full schema (columns added by revisions after 0046, e.g. B20's
    # `parse_progress`), not the frozen 0046 snapshot a bare `select(t.statements_meta)` would
    # otherwise be run against.
    _upgrade(cfg, "head")
    eng.dispose_engine()

    with create_engine(db_url).connect() as conn:
        db_row = conn.execute(
            select(t.statements_meta).where(t.statements_meta.c.id == "stmt_test000001")
        ).one()
        assert db_row.has_parsed_rows == 1
        assert not hasattr(db_row, "parsed_rows_path") or "parsed_rows_path" not in db_row._mapping

    # Proven via the STORE's own read method, not by inspecting the DB row directly.
    from backend.db.statement_store import StatementStore

    monkeypatch.setattr("backend.db.statement_store.DATA_DIR", data_dir)
    monkeypatch.setattr("backend.core.paths.STATEMENTS_FILES_DIR", files_dir / "statements")
    store = StatementStore(engine=create_engine(db_url))
    assert store.find_by_id("stmt_test000001")["parsed_rows"] == parsed_rows

    # Idempotent: re-running is a safe no-op — the file already exists, and the flag is
    # still (re-)confirmed set, never regressed.
    eng.dispose_engine()
    _upgrade(cfg, "202608220859_derived_data_colocation")
    eng.dispose_engine()
    assert new_sidecar.read_text() == new_sidecar.read_text()
    with create_engine(db_url).connect() as conn:
        db_row2 = conn.execute(
            select(t.statements_meta).where(t.statements_meta.c.id == "stmt_test000001")
        ).one()
        assert db_row2.has_parsed_rows == 1


def test_migration_renames_attachment_derived_payload_to_parsed_json(
    db_url, monkeypatch, tmp_path, _isolated_files_dir
):
    from backend.db import engine as eng
    from backend.db import tables as t

    data_dir, files_dir = _isolated_files_dir
    cfg = _seed_at_0045(db_url, monkeypatch, tmp_path, data_dir)

    attachments_dir = files_dir / "attachments"
    attachments_dir.mkdir(parents=True)
    attachment_id = "att_test000001"
    # Post-2026-08-21 TRIMMED shape (plain JSON, no base64 raw bytes) — what 0045 leaves
    # behind, and what this revision renames.
    trimmed_payload = {
        "kind": "text",
        "text": "Date,Amount\n2026-07-01,-12.50",
        "summary": "CSV file 'bank.csv' with 1 rows",
        "format": "csv",
    }
    (attachments_dir / f"{attachment_id}.json").write_text(json.dumps(trimmed_payload))
    (attachments_dir / f"{attachment_id}.csv").write_bytes(b"Date,Amount\n2026-07-01,-12.50")

    engine = create_engine(db_url)
    try:
        with engine.begin() as conn:
            conn.execute(
                insert(t.attachments).values(
                    seq=0,
                    id=attachment_id,
                    payload={
                        "id": attachment_id,
                        "created_at": "2026-08-01T00:00:00Z",
                        "filename": "bank.csv",
                        "kind": "text",
                        "media_type": "text/csv",
                        "summary": trimmed_payload["summary"],
                        "size_bytes": 30,
                        "format": "csv",
                        "content_hash": None,
                        "blob_locator": str(attachments_dir / f"{attachment_id}.csv"),
                        "source_channel": "chat:web",
                        "source_message_id": None,
                        "source_conversation_id": None,
                        "last_used_at": None,
                        "last_used_session": None,
                        "metadata": {},
                    },
                )
            )
    finally:
        engine.dispose()

    eng.dispose_engine()
    _upgrade(cfg, "202608220859_derived_data_colocation")
    eng.dispose_engine()

    new_payload_path = attachments_dir / f"{attachment_id}.parsed.json"
    assert json.loads(new_payload_path.read_text()) == trimmed_payload

    trail = list(attachments_dir.glob(f"{attachment_id}.json.migrated-*"))
    assert len(trail) == 1, "the old <id>.json must be preserved as a trail, never deleted"
    assert json.loads(trail[0].read_text()) == trimmed_payload

    # Proven via AttachmentStore's own read method.
    from backend.db.attachment_store import AttachmentStore

    monkeypatch.setattr("backend.db.attachment_store.ATTACHMENTS_DIR", attachments_dir)
    store = AttachmentStore(engine=create_engine(db_url))
    payload = store.read_payload(attachment_id)
    assert payload["text"] == trimmed_payload["text"]
    assert payload["format"] == "csv"

    # Idempotent re-run.
    eng.dispose_engine()
    _upgrade(cfg, "202608220859_derived_data_colocation")
    eng.dispose_engine()
    assert len(list(attachments_dir.glob(f"{attachment_id}.json.migrated-*"))) == 1


def test_migration_removes_orphaned_statements_parsed_file(
    db_url, monkeypatch, tmp_path, _isolated_files_dir
):
    """A file left in the old ``statements_parsed/`` dir with NO matching row (e.g. a
    stale ``.import-tmp-<hex>`` staged write from an interrupted bundle import, or a
    statement whose DB row was later deleted without its sidecar being cleaned up before
    this migration existed) is renamed ``.removed-<ts>``, never deleted."""
    from backend.db import engine as eng

    data_dir, files_dir = _isolated_files_dir
    cfg = _seed_at_0045(db_url, monkeypatch, tmp_path, data_dir)

    old_parsed_dir = data_dir / "statements_parsed"
    old_parsed_dir.mkdir(parents=True)
    orphan = old_parsed_dir / "stmt_orphan.json"
    orphan.write_text(json.dumps([{"amount": 1.0}]))
    stale_tmp = old_parsed_dir / "stmt_orphan.json.import-tmp-deadbeef"
    stale_tmp.write_text(json.dumps([{"amount": 2.0}]))

    eng.dispose_engine()
    _upgrade(cfg, "202608220859_derived_data_colocation")
    eng.dispose_engine()

    assert not orphan.exists()
    assert not stale_tmp.exists()
    removed = list(old_parsed_dir.glob("stmt_orphan.json.removed-*"))
    removed_tmp = list(old_parsed_dir.glob("stmt_orphan.json.import-tmp-deadbeef.removed-*"))
    assert len(removed) == 1
    assert len(removed_tmp) == 1
    assert json.loads(removed[0].read_text()) == [{"amount": 1.0}]


def test_migration_refuses_a_parsed_rows_path_escaping_the_old_parsed_dir(
    db_url, monkeypatch, tmp_path, _isolated_files_dir
):
    """Security review fix round 2, Fix 1: ``old_path = data_dir / row.parsed_rows_path``
    used to be used directly, with NO containment check, unlike its sibling ``new_path``
    (already wrapped in ``_assert_contained``). A ``parsed_rows_path`` that escapes
    ``old_parsed_dir`` — a DB string, exactly as untrusted as any other value that could
    survive a portable-bundle restore — could copy an arbitrary file's contents into that
    statement's ``parsed_rows`` (an info leak: surfaces on the import-review screen and via
    ``ai/tools/statements.py::_safe_statement``) and then rename the real file away.
    ``_assert_contained`` now refuses this exactly like it already refused an unsafe
    ``new_path``: the row lands in ``report["failed"]``, is never applied, boot does not
    halt, and — proven here — the escape-target file is left COMPLETELY untouched: neither
    its content copied out, nor itself renamed away."""
    from backend.db import engine as eng
    from backend.db import tables as t

    data_dir, files_dir = _isolated_files_dir
    cfg = _seed_at_0045(db_url, monkeypatch, tmp_path, data_dir)

    # A real, sensitive file OUTSIDE old_parsed_dir (a sibling of data_dir) — the canary the
    # escape must never touch, either by reading its content or by renaming it away.
    canary = tmp_path / "onw_secret.json"
    canary_before = json.dumps({"api_token": "REAL-SECRET"})
    canary.write_text(canary_before)

    old_parsed_dir = data_dir / "statements_parsed"
    old_parsed_dir.mkdir(parents=True)
    good_rows = [{"amount": 5.0}]
    (old_parsed_dir / "stmt_good.json").write_text(json.dumps(good_rows))

    evil_row = _statement_row(id="stmt_evil", parsed_rows_path="../onw_secret.json")
    evil_row["payload"]["id"] = "stmt_evil"
    good_row = _statement_row(
        id="stmt_good", seq=1, parsed_rows_path="statements_parsed/stmt_good.json"
    )
    good_row["payload"]["id"] = "stmt_good"

    engine = create_engine(db_url)
    try:
        with engine.begin() as conn:
            conn.execute(insert(_statements_meta_pre46).values(**evil_row))
            conn.execute(insert(_statements_meta_pre46).values(**good_row))
    finally:
        engine.dispose()

    eng.dispose_engine()
    _upgrade(cfg, "202608220859_derived_data_colocation")  # must NOT raise despite the escaping row
    eng.dispose_engine()

    # The canary is completely untouched: same content, still at its original path — not
    # renamed away, not read into anyone's parsed_rows.
    assert canary.exists()
    assert canary.read_text() == canary_before

    # Nothing was ever written for the evil row's id.
    assert not (files_dir / "statements" / "stmt_evil.parsed.json").exists()

    # The good row still migrated normally — one row's refusal never blocks another's.
    new_good = files_dir / "statements" / "stmt_good.parsed.json"
    assert json.loads(new_good.read_text()) == good_rows

    # See test_migration_moves_statement_sidecar_and_sets_has_parsed_rows's own comment: the
    # live `tables.py` describes today's full schema, not the frozen 0046 snapshot.
    _upgrade(cfg, "head")
    eng.dispose_engine()

    with create_engine(db_url).connect() as conn:
        evil_db = conn.execute(
            select(t.statements_meta).where(t.statements_meta.c.id == "stmt_evil")
        ).one()
        good_db = conn.execute(
            select(t.statements_meta).where(t.statements_meta.c.id == "stmt_good")
        ).one()
    assert evil_db.has_parsed_rows == 0, "the escaping row must never be marked migrated"
    assert good_db.has_parsed_rows == 1


def test_migration_survives_malformed_statement_parsed_file_without_halting_boot(
    db_url, monkeypatch, tmp_path, _isolated_files_dir
):
    """A statement row whose ``parsed_rows_path`` points at a non-JSON file must not brick
    the whole boot — it's caught, logged, and counted; the file is left alone (still
    referenced, so the orphan sweep leaves it too) for a human to look at. Every OTHER row
    still migrates normally."""
    from backend.db import engine as eng
    from backend.db import tables as t

    data_dir, files_dir = _isolated_files_dir
    cfg = _seed_at_0045(db_url, monkeypatch, tmp_path, data_dir)

    old_parsed_dir = data_dir / "statements_parsed"
    old_parsed_dir.mkdir(parents=True)
    (old_parsed_dir / "stmt_bad.json").write_text("{not valid json")
    good_rows = [{"amount": 5.0}]
    (old_parsed_dir / "stmt_good.json").write_text(json.dumps(good_rows))

    bad_row = _statement_row(id="stmt_bad", parsed_rows_path="statements_parsed/stmt_bad.json")
    bad_row["payload"]["id"] = "stmt_bad"
    good_row = _statement_row(
        id="stmt_good", seq=1, parsed_rows_path="statements_parsed/stmt_good.json"
    )
    good_row["payload"]["id"] = "stmt_good"

    engine = create_engine(db_url)
    try:
        with engine.begin() as conn:
            conn.execute(insert(_statements_meta_pre46).values(**bad_row))
            conn.execute(insert(_statements_meta_pre46).values(**good_row))
    finally:
        engine.dispose()

    eng.dispose_engine()
    _upgrade(
        cfg, "202608220859_derived_data_colocation"
    )  # must NOT raise despite the malformed row
    eng.dispose_engine()

    # The good row migrated normally.
    new_good = files_dir / "statements" / "stmt_good.parsed.json"
    assert json.loads(new_good.read_text()) == good_rows

    # The malformed row's source file is untouched (never renamed away — still referenced).
    assert (old_parsed_dir / "stmt_bad.json").exists()
    assert not (files_dir / "statements" / "stmt_bad.parsed.json").exists()

    # See test_migration_moves_statement_sidecar_and_sets_has_parsed_rows's own comment: the
    # live `tables.py` describes today's full schema, not the frozen 0046 snapshot.
    _upgrade(cfg, "head")
    eng.dispose_engine()

    with create_engine(db_url).connect() as conn:
        bad_db = conn.execute(
            select(t.statements_meta).where(t.statements_meta.c.id == "stmt_bad")
        ).one()
        good_db = conn.execute(
            select(t.statements_meta).where(t.statements_meta.c.id == "stmt_good")
        ).one()
    assert bad_db.has_parsed_rows == 0
    assert good_db.has_parsed_rows == 1


def test_migration_is_a_noop_on_a_fresh_database(
    db_url, monkeypatch, tmp_path, _isolated_files_dir
):
    """No statements/attachments rows at all — the common case (a `db_engine`/`db_isolated`
    fresh test DB, and a genuinely fresh real install) must not fail or fabricate files.
    This is exercised implicitly by EVERY OTHER test in the suite via db_isolated/db_engine
    (both now run through revision 0046 as part of "head"); asserted explicitly here too."""
    from backend.db import engine as eng

    data_dir, files_dir = _isolated_files_dir
    cfg = _seed_at_0045(db_url, monkeypatch, tmp_path, data_dir)

    eng.dispose_engine()
    _upgrade(cfg, "202608220859_derived_data_colocation")
    eng.dispose_engine()


def test_check_constraint_rejects_an_out_of_range_has_parsed_rows(
    db_url, monkeypatch, tmp_path, _isolated_files_dir
):
    """The CheckConstraint added by this revision is real DDL, not just documentation."""
    import pytest as _pytest
    from sqlalchemy.exc import IntegrityError

    from backend.db import engine as eng
    from backend.db import tables as t

    data_dir, files_dir = _isolated_files_dir
    cfg = _seed_at_0045(db_url, monkeypatch, tmp_path, data_dir)
    eng.dispose_engine()
    _upgrade(cfg, "202608220859_derived_data_colocation")
    eng.dispose_engine()

    row = _statement_row(id="stmt_ck", has_parsed_rows=2)
    del row["parsed_rows_path"]  # dropped by 0046 — this insert targets the POST-46 schema
    row["payload"]["id"] = "stmt_ck"
    engine = create_engine(db_url)
    try:
        with _pytest.raises(IntegrityError):
            with engine.begin() as conn:
                conn.execute(insert(t.statements_meta).values(**row))
    finally:
        engine.dispose()


def test_statements_files_dir_registered_as_intentionally_file_backed():
    """RFC §3 Option A cost: ``STATEMENTS_FILES_DIR`` now holds ``.parsed.json`` files
    alongside raw statement files, so it must join ``INTENTIONALLY_FILE_BACKED`` or
    ``tests/db/test_no_stray_json_store.py`` would flag every ``.parsed.json`` as a stray.

    Deliberately does NOT import the module-level ``STATEMENTS_FILES_DIR`` name — the
    suite's autouse ``_isolate_statement_files`` fixture (tests/conftest.py) monkeypatches
    THAT symbol to a per-test throwaway directory, which is never a member of
    ``INTENTIONALLY_FILE_BACKED`` (computed once, at import time, from the REAL path).
    ``FILES_DIR`` is untouched by that fixture, so recomputing the same
    ``FILES_DIR / "statements"`` join it's built from gets the real, registered value."""
    from backend.core.paths import FILES_DIR, is_intentionally_file_backed

    assert is_intentionally_file_backed(FILES_DIR / "statements" / "stmt_abc123.parsed.json")


def test_statements_parsed_dir_constant_is_retired():
    import backend.core.paths as paths_mod

    assert not hasattr(paths_mod, "STATEMENTS_PARSED_DIR")


def test_statements_files_dir_accessor_is_the_single_binding(tmp_path, monkeypatch):
    """Security review fix round 2, Fix 3: every consumer of the statements blob directory
    calls ``core.paths.statements_files_dir()`` — a bare accessor resolved at call time —
    instead of importing the ``STATEMENTS_FILES_DIR`` constant into its own module
    namespace. Patching ONLY ``core.paths.STATEMENTS_FILES_DIR`` must be visible through
    every consumer's own accessor call, proving there is exactly ONE binding left to patch,
    not one per consumer module. Before this fix, each of these three modules held its OWN
    separately-imported ``STATEMENTS_FILES_DIR`` name — patching only ``core.paths`` here
    would have left ``money_io``/``statement_store`` still pointed at the ORIGINAL directory,
    a silent split-brain (e.g. ``money_io._reinline_statement`` degrading to
    ``parsed_rows=None`` instead of raising)."""
    import backend.core.paths as core_paths_mod
    import backend.db.money_io as money_io_mod
    import backend.db.statement_store as statement_store_mod

    new_dir = tmp_path / "patched_statements"
    monkeypatch.setattr(core_paths_mod, "STATEMENTS_FILES_DIR", new_dir)

    assert core_paths_mod.statements_files_dir() == new_dir
    assert money_io_mod.statements_files_dir() == new_dir
    assert statement_store_mod.statements_files_dir() == new_dir


def test_already_migrated_branch_reconfirms_has_parsed_rows_after_a_rolled_back_update(
    db_url, monkeypatch, tmp_path, _isolated_files_dir
):
    """CRASH-THEN-RETRY regression: ``upgrade()`` runs inside ONE database transaction, but
    the file writes/renames in ``_migrate_one_statement_row`` are NOT part of it. If a LATER
    row in the same run raises and aborts the whole revision, every row already migrated
    before it keeps its real ``<id>.parsed.json`` file on disk while its
    ``has_parsed_rows=1`` UPDATE rolls back with everything else. A retry must find the file
    already present (the ``already_migrated`` branch) and still RE-CONFIRM
    ``has_parsed_rows=1`` — not silently leave a row with a real file on disk but
    ``has_parsed_rows=0`` forever, which would make ``StatementStore`` treat it as
    never-parsed."""
    import importlib.util
    from pathlib import Path as _Path

    from sqlalchemy import text as sa_text

    data_dir, files_dir = _isolated_files_dir
    _seed_at_0045(db_url, monkeypatch, tmp_path, data_dir)

    statements_dir = files_dir / "statements"
    statements_dir.mkdir(parents=True, exist_ok=True)
    sid = "stmt_retry001"
    parsed_rows = [{"amount": 1.0}]
    # The PRIOR (interrupted) attempt already wrote the new file — file writes are not
    # transactional and do not roll back.
    (statements_dir / f"{sid}.parsed.json").write_text(json.dumps(parsed_rows))

    engine = create_engine(db_url)
    try:
        with engine.begin() as conn:
            conn.execute(
                insert(_statements_meta_pre46).values(
                    id=sid,
                    book_id="book_000000000000",
                    seq=0,
                    created_at="2026-08-01T00:00:00Z",
                    filename="x.pdf",
                    mime="application/pdf",
                    size_bytes=1,
                    file_hash="h",
                    file_path=f"files/statements/{sid}.pdf",
                    kind="pdf_pages",
                    account_id=None,
                    parsed_at=None,
                    status=None,
                    meta_unavailable=0,
                    import_meta=None,
                    batch_ids=[],
                    parsed_rows_path=f"statements_parsed/{sid}.json",  # still-live pointer
                    payload={"id": sid},
                )
            )
    finally:
        engine.dispose()

    mod_path = (
        _Path(__file__).resolve().parents[2]
        / "backend"
        / "db"
        / "alembic"
        / "versions"
        / "202608220859_derived_data_colocation.py"
    )
    spec = importlib.util.spec_from_file_location("_migration_0046_retry_test", mod_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    engine = create_engine(db_url)
    try:
        with engine.begin() as conn:
            # Mimics the mid-transaction state a real retry would find: the column has been
            # (re-)added by op.add_column, with its server_default of 0 — as if the previous
            # attempt's has_parsed_rows=1 UPDATE for this row had rolled back.
            conn.execute(
                sa_text(
                    "ALTER TABLE statements_meta ADD COLUMN has_parsed_rows INTEGER NOT NULL "
                    "DEFAULT 0"
                )
            )
            row = conn.execute(
                sa_text("SELECT id, parsed_rows_path FROM statements_meta WHERE id = :id"),
                {"id": sid},
            ).one()
            status = mod._migrate_one_statement_row(
                conn, row, statements_dir, data_dir, data_dir / "statements_parsed", mod._stamp()
            )
            assert status == "already_migrated"
            confirmed = conn.execute(
                sa_text("SELECT has_parsed_rows FROM statements_meta WHERE id = :id"), {"id": sid}
            ).scalar_one()
            assert confirmed == 1, (
                "a retry that finds the file already migrated must still (re-)confirm "
                "has_parsed_rows=1"
            )
    finally:
        engine.dispose()
