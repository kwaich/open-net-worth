"""Revision 0045 (``backend.db.alembic.versions.202608220022_files_dir_and_raw_blob_storage``) —
the four-part migration behind
``docs/adr/2026-08-21-unify-raw-file-storage-across-blob-stores.md``: merge
artifacts/attachments/receipts/statements under one shared ``DATA_DIR/files/`` parent,
rewrite the statements/artifacts stored ``file_path`` references to match (a
post-security-review fix — step 1 alone relocated the bytes but left the DB pointed at the
old location), give every pre-existing receipt its real extension (instead of a hardcoded
``.bin``), and extract every pre-existing attachment's raw bytes out of its JSON payload
onto a real file (per-row fault-tolerant — a malformed row is skipped and reported, never
allowed to halt every future boot).

Runs the migration for REAL (``command.upgrade`` from ``"202608201228_rename_signals_to_notification_log"`` to ``"202608220022_files_dir_and_raw_blob_storage"`` — pinned to
this revision specifically, NOT ``"head"``, since revision 0046 (2026-08-22
consolidate-derived-data-storage) immediately renames the very ``<id>.json`` attachment
sidecar this file's own assertions inspect; pinning keeps this file proving 0045's own
output shape regardless of what a later revision does to it — on an isolated tmp-path DB +
tmp-path DATA_DIR — never the live tree) against hand-seeded PRE-migration rows/files,
mirroring ``tests/db/test_money_cutover.py``'s own cutover-test shape. This is the closest a
unit test gets to "run it against a copy of real data and prove nothing was lost" — the full
end-to-end proof against an actual isolated COPY of a real ``~/.open-net-worth`` tree is the
separate manual verification step described in the task report, not something a fast unit
test can safely automate.
"""

from __future__ import annotations

import base64
import json

import pytest
from sqlalchemy import create_engine, insert, select

from tests.db.conftest import patch_all_cutover_sources_missing

# These walk the chain from an old revision themselves, so on Postgres (one shared database
# that earlier tests leave at head) they must start from an empty schema.
pytestmark = pytest.mark.usefixtures("scrubbed_db_url")


@pytest.fixture
def _isolated_files_dir(tmp_path, monkeypatch):
    import backend.core.paths as paths_mod

    data_dir = tmp_path / "onw_data"
    files_dir = data_dir / "files"
    data_dir.mkdir(parents=True, exist_ok=True)  # revision 0038 iterdir()s DATA_DIR
    monkeypatch.setattr(paths_mod, "DATA_DIR", data_dir)
    monkeypatch.setattr(paths_mod, "FILES_DIR", files_dir)
    # The per-store blob dirs are computed FROM `FILES_DIR` at import time, so patching
    # `FILES_DIR` alone leaves them pointing at the real tree — and `statements_files_dir()`
    # (the accessor every consumer is required to call) returns `STATEMENTS_FILES_DIR`
    # directly. Patch that constant too, exactly as its docstring prescribes, or a reader
    # that honours the accessor convention looks outside this fixture's isolation.
    monkeypatch.setattr(paths_mod, "STATEMENTS_FILES_DIR", files_dir / "statements")
    monkeypatch.setattr(paths_mod, "ARTIFACTS_DIR", files_dir / "artifacts")
    return data_dir, files_dir


def _upgrade(cfg, revision):
    from alembic import command

    command.upgrade(cfg, revision)


def test_migration_relocates_dirs_fixes_receipt_ext_and_trims_attachment_payload(
    db_url, monkeypatch, tmp_path, _isolated_files_dir
):
    from backend.db import engine as eng
    from backend.db import tables as t
    from backend.db.alembic_config import make_config

    data_dir, files_dir = _isolated_files_dir
    monkeypatch.setenv("ONW_DATABASE_URL", db_url)
    patch_all_cutover_sources_missing(monkeypatch, tmp_path)
    cfg = make_config(db_url)

    eng.dispose_engine()
    _upgrade(
        cfg, "202608201228_rename_signals_to_notification_log"
    )  # everything EXCEPT this migration

    # ── Seed pre-migration state: old flat dirs + old-shape files + old DB rows ──────
    old_receipts_dir = data_dir / "receipts"
    old_receipts_dir.mkdir(parents=True)
    old_attachments_dir = data_dir / "attachments"
    old_attachments_dir.mkdir(parents=True)

    receipt_id = "rcpt_test000001"
    receipt_bytes = b"\xff\xd8\xff\xe0jpeg-bytes-here"
    (old_receipts_dir / f"{receipt_id}.bin").write_bytes(receipt_bytes)

    attachment_id = "att_test000001"
    raw_csv = b"Date,Amount\n2026-07-01,-12.50"
    old_attachment_payload = {
        "kind": "text",
        "text": raw_csv.decode(),
        "summary": "CSV file 'bank.csv' with 1 rows",
        "format": "csv",
        "content_b64": base64.b64encode(raw_csv).decode(),
    }
    (old_attachments_dir / f"{attachment_id}.json").write_text(json.dumps(old_attachment_payload))

    # A second attachment: kind="image", using the pre-migration `data` (plain base64,
    # never wrapped as content_b64) shape.
    image_id = "att_test000002"
    image_bytes = b"\x89PNG\r\n\x1a\nfake-png-bytes"
    old_image_payload = {
        "kind": "image",
        "media_type": "image/png",
        "data": base64.standard_b64encode(image_bytes).decode(),
        "summary": "Image (pic.png, 1KB)",
    }
    (old_attachments_dir / f"{image_id}.json").write_text(json.dumps(old_image_payload))

    engine = create_engine(db_url)
    try:
        with engine.begin() as conn:
            conn.execute(
                insert(t.receipts).values(
                    seq=0,
                    id=receipt_id,
                    payload={
                        "id": receipt_id,
                        "created_at": "2026-08-01T00:00:00Z",
                        "filename": "receipt.jpg",
                        "media_type": "image/jpeg",
                        "size_bytes": len(receipt_bytes),
                        "content_hash": None,
                        "blob_locator": str(old_receipts_dir / f"{receipt_id}.bin"),
                        "source_attachment_id": None,
                        "metadata": {},
                    },
                )
            )
            conn.execute(
                insert(t.attachments).values(
                    [
                        {
                            "seq": 0,
                            "id": attachment_id,
                            "payload": {
                                "id": attachment_id,
                                "created_at": "2026-08-01T00:00:00Z",
                                "filename": "bank.csv",
                                "kind": "text",
                                "media_type": "text/csv",
                                "summary": "CSV file 'bank.csv' with 1 rows",
                                "size_bytes": 999,
                                "format": "csv",
                                "content_hash": None,
                                "blob_locator": str(old_attachments_dir / f"{attachment_id}.json"),
                                "source_channel": "chat:web",
                                "source_message_id": None,
                                "source_conversation_id": None,
                                "last_used_at": None,
                                "last_used_session": None,
                                "metadata": {},
                            },
                        },
                        {
                            "seq": 1,
                            "id": image_id,
                            "payload": {
                                "id": image_id,
                                "created_at": "2026-08-01T00:00:00Z",
                                "filename": "pic.png",
                                "kind": "image",
                                "media_type": "image/png",
                                "summary": "Image (pic.png, 1KB)",
                                "size_bytes": 999,
                                "format": None,
                                "content_hash": None,
                                "blob_locator": str(old_attachments_dir / f"{image_id}.json"),
                                "source_channel": "chat:web",
                                "source_message_id": None,
                                "source_conversation_id": None,
                                "last_used_at": None,
                                "last_used_session": None,
                                "metadata": {},
                            },
                        },
                    ]
                )
            )
    finally:
        engine.dispose()

    eng.dispose_engine()
    _upgrade(cfg, "202608220022_files_dir_and_raw_blob_storage")  # runs revision 0045
    eng.dispose_engine()

    # ── 1. Directory move ────────────────────────────────────────────────────────────
    assert files_dir.is_dir()
    new_receipts_dir = files_dir / "receipts"
    new_attachments_dir = files_dir / "attachments"
    assert new_receipts_dir.is_dir()
    assert new_attachments_dir.is_dir()
    # The old flat dirs are emptied (every item relocated) and removed.
    assert not old_receipts_dir.exists()
    assert not old_attachments_dir.exists()

    # ── 2. Receipt extension fix — byte-identical, original LEFT IN PLACE (never deleted) ──
    old_bin = new_receipts_dir / f"{receipt_id}.bin"
    new_jpg = new_receipts_dir / f"{receipt_id}.jpg"
    assert old_bin.exists(), "original .bin must be left as a trail, never destroyed"
    assert old_bin.read_bytes() == receipt_bytes
    assert new_jpg.exists()
    assert new_jpg.read_bytes() == receipt_bytes

    # ── 3. Attachment payload trim + raw-bytes extraction (text/csv kind) ─────────────
    new_csv = new_attachments_dir / f"{attachment_id}.csv"
    assert new_csv.read_bytes() == raw_csv
    trimmed = json.loads((new_attachments_dir / f"{attachment_id}.json").read_text())
    assert "content_b64" not in trimmed
    assert "content" not in trimmed
    assert "data" not in trimmed
    assert trimmed["text"] == raw_csv.decode()
    assert trimmed["format"] == "csv"

    # size_bytes on the DB metadata row was minted from the OLD base64+JSON-wrapped
    # payload (seeded above as a deliberately stale 999) — must be corrected to the true
    # raw-file size, not left inflated forever.
    with create_engine(db_url).connect() as conn:
        att_row = conn.execute(
            select(t.attachments.c.payload).where(t.attachments.c.id == attachment_id)
        ).first()
        assert att_row.payload["size_bytes"] == len(raw_csv)

    backups = list(new_attachments_dir.glob(f"{attachment_id}.json.pre-raw-file-migration-*"))
    assert len(backups) == 1, "the OLD full payload must be preserved as a trail, never lost"
    assert json.loads(backups[0].read_text()) == old_attachment_payload

    # ── 4. Attachment payload trim (image kind, plain `data` shape) ───────────────────
    new_png = new_attachments_dir / f"{image_id}.png"
    assert new_png.read_bytes() == image_bytes
    trimmed_image = json.loads((new_attachments_dir / f"{image_id}.json").read_text())
    assert "data" not in trimmed_image
    assert trimmed_image["kind"] == "image"

    # ── 5. Re-running the migration a second time is a safe no-op (idempotent) ────────
    eng.dispose_engine()
    _upgrade(cfg, "202608220022_files_dir_and_raw_blob_storage")
    eng.dispose_engine()
    assert new_jpg.read_bytes() == receipt_bytes
    assert new_csv.read_bytes() == raw_csv
    assert (
        len(list(new_attachments_dir.glob(f"{attachment_id}.json.pre-raw-file-migration-*"))) == 1
    )

    # Store-level read paths agree with what the migration produced.
    with create_engine(db_url).connect() as conn:
        row = conn.execute(select(t.receipts).where(t.receipts.c.id == receipt_id)).first()
        assert row is not None


def test_migration_rewrites_statement_and_artifact_file_paths(
    db_url, monkeypatch, tmp_path, _isolated_files_dir
):
    """Security-review Fix 1: ``upgrade()`` relocates the BYTES for statements/artifacts
    (like every other store) but, unlike receipts/attachments, never touched the stored
    ``file_path`` reference — permanently orphaning every pre-existing statement/artifact
    (``StatementStore.read_file_bytes``/``ArtifactStore.read_text`` resolve a path that no
    longer exists) and making ``StatementStore.delete_statement`` unlink nothing while still
    dropping the DB row (a real-file leak that then rides every DR backup). Proven here via
    the STORES' OWN read methods against real seeded pre-migration rows — the reviewer's own
    stated gap ("the migration test seeds only receipts and attachments rows").
    """
    from backend.db import engine as eng
    from backend.db import tables as t
    from backend.db.alembic_config import make_config

    data_dir, files_dir = _isolated_files_dir
    monkeypatch.setenv("ONW_DATABASE_URL", db_url)
    patch_all_cutover_sources_missing(monkeypatch, tmp_path)
    cfg = make_config(db_url)

    eng.dispose_engine()
    _upgrade(
        cfg, "202608201228_rename_signals_to_notification_log"
    )  # everything EXCEPT this migration

    # ── Seed pre-migration state: old flat dirs + real files + old file_path values ────
    old_statements_dir = data_dir / "statements"
    old_statements_dir.mkdir(parents=True)
    old_artifacts_dir = data_dir / "artifacts"
    old_artifacts_dir.mkdir(parents=True)

    statement_id = "stmt_test000001"
    statement_bytes = b"%PDF-1.4 fake bank statement"
    (old_statements_dir / f"{statement_id}.pdf").write_bytes(statement_bytes)

    artifact_id = "art_test000001"
    artifact_html = "<!doctype html><html><body>chart</body></html>"
    (old_artifacts_dir / f"{artifact_id}.html").write_text(artifact_html)

    engine = create_engine(db_url)
    try:
        with engine.begin() as conn:
            statement_payload = {
                "id": statement_id,
                "created_at": "2026-08-01T00:00:00Z",
                "filename": "statement.pdf",
                "mime": "application/pdf",
                "size_bytes": len(statement_bytes),
                "file_hash": "deadbeef",
                "file_path": f"statements/{statement_id}.pdf",  # OLD relative form
                "kind": "pdf_pages",
                "upload_entry": {},
                "parsed_rows": None,
                "parsed_at": None,
                "account_id": None,
                "batch_ids": [],
                "source_attachment_id": None,
            }
            conn.execute(
                insert(t.statements_meta).values(
                    id=statement_id,
                    book_id="book_000000000000",
                    seq=0,
                    created_at="2026-08-01T00:00:00Z",
                    filename="statement.pdf",
                    mime="application/pdf",
                    size_bytes=len(statement_bytes),
                    file_hash="deadbeef",
                    file_path=f"statements/{statement_id}.pdf",  # OLD relative form
                    kind="pdf_pages",
                    account_id=None,
                    parsed_at=None,
                    status=None,
                    meta_unavailable=0,
                    import_meta=None,
                    batch_ids=[],
                    # NOT `parsed_rows_path=None`: revision 0046 (a later revision than the
                    # one under test here) removes that column from `backend.db.tables`'
                    # live Python description, so it can no longer be named in a `.values()`
                    # call — the real DB schema at THIS point in the chain (still "202608220022_files_dir_and_raw_blob_storage")
                    # still has the column, and leaving it unmentioned defaults it to NULL,
                    # equivalent to the old explicit `None`.
                    payload=statement_payload,
                )
            )
            conn.execute(
                insert(t.artifacts).values(
                    seq=0,
                    id=artifact_id,
                    payload={
                        "id": artifact_id,
                        "created_at": "2026-08-01T00:00:00Z",
                        "session_id": None,
                        "title": "Chart",
                        "filename": "chart.html",
                        "artifact_type": "html",
                        "size_bytes": len(artifact_html.encode("utf-8")),
                        "file_path": f"artifacts/{artifact_id}.html",  # OLD relative form
                    },
                )
            )
    finally:
        engine.dispose()

    eng.dispose_engine()
    # "head", not "202608220022_files_dir_and_raw_blob_storage" (unlike the attachment-payload tests in this file): this test
    # verifies file_path rewriting via StatementStore/ArtifactStore's OWN read methods,
    # which assume the CURRENT (post-0046) schema unconditionally — and revision 0046
    # doesn't disturb file_path or artifacts at all (only statements' parsed-rows
    # column/sidecar and attachments' payload naming), so running the whole chain here is
    # both safe and necessary.
    _upgrade(cfg, "head")  # runs revisions 0045 AND 0046
    eng.dispose_engine()

    # ── The bytes moved (step 1) ────────────────────────────────────────────────────
    new_statement_file = files_dir / "statements" / f"{statement_id}.pdf"
    new_artifact_file = files_dir / "artifacts" / f"{artifact_id}.html"
    assert new_statement_file.read_bytes() == statement_bytes
    assert new_artifact_file.read_text() == artifact_html

    # ── The stored references were rewritten too (Fix 1) — proven via the STORES' OWN
    # read methods, not by inspecting the DB row directly. ────────────────────────────
    from backend.db.artifact_store import ArtifactStore
    from backend.db.statement_store import StatementStore

    monkeypatch.setattr("backend.db.statement_store.DATA_DIR", data_dir)
    monkeypatch.setattr("backend.db.artifact_store.DATA_DIR", data_dir)

    stmt_store = StatementStore(engine=create_engine(db_url))
    art_store = ArtifactStore(engine=create_engine(db_url))

    stmt_record = stmt_store.find_by_id(statement_id)
    assert stmt_record["file_path"] == f"files/statements/{statement_id}.pdf"
    assert stmt_store.read_file_bytes(statement_id) == statement_bytes

    art_record = art_store.find_by_id(artifact_id)
    assert art_record["file_path"] == f"files/artifacts/{artifact_id}.html"
    assert art_store.read_text(artifact_id) == artifact_html

    # The promoted statements_meta.file_path COLUMN is also kept in sync (not just payload).
    with create_engine(db_url).connect() as conn:
        col_row = conn.execute(
            select(t.statements_meta.c.file_path).where(t.statements_meta.c.id == statement_id)
        ).first()
        assert col_row.file_path == f"files/statements/{statement_id}.pdf"

    # ── Idempotent: re-running the migration must not double-prefix the path ──────────
    eng.dispose_engine()
    _upgrade(cfg, "head")
    eng.dispose_engine()
    stmt_store2 = StatementStore(engine=create_engine(db_url))
    art_store2 = ArtifactStore(engine=create_engine(db_url))
    assert (
        stmt_store2.find_by_id(statement_id)["file_path"] == f"files/statements/{statement_id}.pdf"
    )
    assert art_store2.find_by_id(artifact_id)["file_path"] == f"files/artifacts/{artifact_id}.html"


def _import_migration_module():
    """Load revision 0045's module directly (no DB/alembic bootstrap needed) so
    ``_merge_move_dir`` — pure filesystem logic — can be unit-tested in isolation, mirroring
    ``test_attachment_store_migration_blob_dir.py``'s own ``importlib`` pattern for a
    filename that can't be a normal Python identifier."""
    import importlib.util
    from pathlib import Path

    path = (
        Path(__file__).resolve().parents[2]
        / "backend"
        / "db"
        / "alembic"
        / "versions"
        / "202608220022_files_dir_and_raw_blob_storage.py"
    )
    spec = importlib.util.spec_from_file_location("_migration_0045_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_merge_move_dir_skips_source_symlinks_and_respects_dangling_dest_symlink(tmp_path):
    """LOW security-review finding: a dangling symlink at the destination reads as ABSENT
    under ``Path.exists()`` (it follows the link to a target that doesn't exist), which
    would let ``shutil.move`` proceed against it in a confusing way — ``os.path.lexists``
    sees the link itself instead. A symlink in the SOURCE is skipped-and-reported, never
    blindly relocated."""
    mod = _import_migration_module()

    old_dir = tmp_path / "old"
    new_dir = tmp_path / "new"
    old_dir.mkdir()
    new_dir.mkdir()

    (old_dir / "real.txt").write_text("keep me")

    outside_target = tmp_path / "outside.txt"
    outside_target.write_text("should not be relocated")
    symlink_source = old_dir / "linked.txt"
    symlink_source.symlink_to(outside_target)

    # A dangling symlink already at the destination for a name that ALSO exists in the
    # source — lexists must treat it as "already present" (never clobbered/double-moved)
    # even though Path.exists() would say it's absent.
    dangling_name = "dangling.txt"
    (old_dir / dangling_name).write_text("source copy")
    (new_dir / dangling_name).symlink_to(tmp_path / "does-not-exist.txt")

    mod._merge_move_dir(old_dir, new_dir)

    # The real file relocated normally.
    assert (new_dir / "real.txt").read_text() == "keep me"
    assert not (old_dir / "real.txt").exists()

    # The symlink was left in the SOURCE, never relocated or followed.
    assert symlink_source.is_symlink()
    assert outside_target.read_text() == "should not be relocated"
    # old_dir is therefore not empty and not removed.
    assert old_dir.exists()

    # The dangling destination symlink blocked the move — the source copy is left behind,
    # untouched, never silently clobbering the existing (broken) link.
    assert (old_dir / dangling_name).exists()
    assert (new_dir / dangling_name).is_symlink()


def test_migration_survives_malformed_attachment_rows_without_halting_boot(
    db_url, monkeypatch, tmp_path, _isolated_files_dir
):
    """Security-review Fix 4: ``_fix_attachment_payloads`` previously had no per-row error
    handling — ``ensure_migrated()`` is fail-CLOSED, so an uncaught exception on ONE bad row
    (invalid JSON, or a "kind": "image" row with "data": null — the skip-guard doesn't catch
    that shape, and ``base64.b64decode(None)`` raises an uncaught TypeError) refused to boot
    FOREVER, not just once. A malformed row must now be caught, logged, and skipped —
    letting every other row (and the rest of boot) proceed."""
    from backend.db import engine as eng
    from backend.db import tables as t
    from backend.db.alembic_config import make_config

    data_dir, files_dir = _isolated_files_dir
    monkeypatch.setenv("ONW_DATABASE_URL", db_url)
    patch_all_cutover_sources_missing(monkeypatch, tmp_path)
    cfg = make_config(db_url)

    eng.dispose_engine()
    _upgrade(cfg, "202608201228_rename_signals_to_notification_log")

    old_attachments_dir = data_dir / "attachments"
    old_attachments_dir.mkdir(parents=True)

    bad_json_id = "att_badjson0001"
    (old_attachments_dir / f"{bad_json_id}.json").write_text("{not valid json")

    null_data_id = "att_nulldata001"
    (old_attachments_dir / f"{null_data_id}.json").write_text(
        json.dumps({"kind": "image", "media_type": "image/png", "data": None})
    )

    good_id = "att_goodrow0001"
    good_raw = b"Date,Amount\n2026-08-01,-5.00"
    (old_attachments_dir / f"{good_id}.json").write_text(
        json.dumps(
            {
                "kind": "text",
                "text": good_raw.decode(),
                "format": "csv",
                "content_b64": base64.b64encode(good_raw).decode(),
            }
        )
    )

    def _row(seq, aid, filename=None, media_type=None):
        return {
            "seq": seq,
            "id": aid,
            "payload": {
                "id": aid,
                "created_at": "2026-08-01T00:00:00Z",
                "filename": filename or f"{aid}.dat",
                "kind": "text",
                "media_type": media_type,
                "summary": "",
                "size_bytes": 999,
                "format": None,
                "content_hash": None,
                "blob_locator": "",
                "source_channel": "chat:web",
                "source_message_id": None,
                "source_conversation_id": None,
                "last_used_at": None,
                "last_used_session": None,
                "metadata": {},
            },
        }

    engine = create_engine(db_url)
    try:
        with engine.begin() as conn:
            conn.execute(
                insert(t.attachments).values(
                    [
                        _row(0, bad_json_id),
                        _row(1, null_data_id),
                        _row(2, good_id, filename="bank.csv", media_type="text/csv"),
                    ]
                )
            )
    finally:
        engine.dispose()

    eng.dispose_engine()
    _upgrade(
        cfg, "202608220022_files_dir_and_raw_blob_storage"
    )  # must NOT raise despite the two malformed rows
    eng.dispose_engine()

    new_attachments_dir = files_dir / "attachments"
    # The good row still migrated normally.
    assert (new_attachments_dir / f"{good_id}.csv").read_bytes() == good_raw
    trimmed = json.loads((new_attachments_dir / f"{good_id}.json").read_text())
    assert "content_b64" not in trimmed

    # The malformed rows were left alone (no raw file conjured from nothing) rather than
    # crashing the whole migration.
    assert not list(new_attachments_dir.glob(f"{bad_json_id}.*ext_never_created*"))
    assert not (new_attachments_dir / f"{null_data_id}.png").exists()

    # A second run is also safe (the malformed rows are re-attempted and fail the same way
    # every time, never wedging the boot gate).
    eng.dispose_engine()
    _upgrade(cfg, "202608220022_files_dir_and_raw_blob_storage")
    eng.dispose_engine()


def test_migration_is_a_noop_on_a_fresh_database(
    db_url, monkeypatch, tmp_path, _isolated_files_dir
):
    """No receipts/attachments rows at all — the common case (a `db_engine`/`db_isolated`
    fresh test DB, and a genuinely fresh real install) must not fail or fabricate files."""
    from backend.db import engine as eng
    from backend.db.alembic_config import make_config

    monkeypatch.setenv("ONW_DATABASE_URL", db_url)
    patch_all_cutover_sources_missing(monkeypatch, tmp_path)
    cfg = make_config(db_url)

    eng.dispose_engine()
    _upgrade(cfg, "202608220022_files_dir_and_raw_blob_storage")
    eng.dispose_engine()
