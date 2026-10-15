"""backend/scripts/purge_migration_trail.py — revision 0045's leftover migration-trail
cleanup (receipts' old `.bin`, attachments' `.pre-raw-file-migration-<ts>` backup).

Every test proves the mechanical safety contract: a trail file is deleted ONLY when its
replacement is freshly re-verified (byte-identical for receipts, a real read-path
round-trip for attachments) right before deletion — a byte-mismatched or otherwise
unproven replacement is always skipped, never deleted, in both dry-run and --execute.
"""

from __future__ import annotations

import json

import pytest

from backend.scripts import purge_migration_trail as script


@pytest.fixture
def stores(tmp_path, monkeypatch, db_isolated):
    import backend.db.attachment_store as attachment_store_mod
    import backend.db.receipt_store as receipt_store_mod

    rcpt_dir = tmp_path / "receipts"
    rcpt_dir.mkdir()
    att_dir = tmp_path / "attachments"
    att_dir.mkdir()
    monkeypatch.setattr(receipt_store_mod, "RECEIPTS_DIR", rcpt_dir)
    monkeypatch.setattr(attachment_store_mod, "ATTACHMENTS_DIR", att_dir)
    return receipt_store_mod.ReceiptStore(), attachment_store_mod.AttachmentStore()


def _receipts_dir(stores):
    import backend.db.receipt_store as receipt_store_mod

    return receipt_store_mod.RECEIPTS_DIR


def _attachments_dir(stores):
    import backend.db.attachment_store as attachment_store_mod

    return attachment_store_mod.ATTACHMENTS_DIR


@pytest.fixture
def statement_setup(tmp_path, monkeypatch, db_isolated):
    """Fix 8 (security review round 2): revision 0046's own ``statements_parsed/`` trail —
    a SEPARATE directory from ``RECEIPTS_DIR``/``ATTACHMENTS_DIR`` above, since a statement's
    trail lives OUTSIDE its blob store's own directory (unlike receipts/attachments, whose
    trails are colocated). Patches the ONE real accessor binding
    (``core.paths.STATEMENTS_FILES_DIR``, Fix 3) plus ``DATA_DIR`` — both read live by
    ``purge_migration_trail.run()``."""
    import backend.core.paths as core_paths_mod

    data_dir = tmp_path / "onw_data"
    data_dir.mkdir()
    stmts_dir = data_dir / "files" / "statements"
    stmts_dir.mkdir(parents=True)
    monkeypatch.setattr(core_paths_mod, "DATA_DIR", data_dir)
    monkeypatch.setattr(core_paths_mod, "STATEMENTS_FILES_DIR", stmts_dir)
    return data_dir, stmts_dir


class TestReceiptTrail:
    def test_verified_good_trail_is_a_dry_run_candidate_not_deleted(self, stores):
        receipt_store, _ = stores
        ref = receipt_store.create(b"jpeg-bytes", filename="r.jpg", media_type="image/jpeg")
        old_bin = _receipts_dir(stores) / f"{ref}.bin"
        old_bin.write_bytes(b"jpeg-bytes")  # byte-identical to the real .jpg

        results = script.run(execute=False)
        hit = next(r for r in results if r.kind == "receipt" and r.id == ref)

        assert hit.status == "would_delete"
        assert hit.bytes_reclaimed == len(b"jpeg-bytes")
        assert old_bin.exists(), "dry-run must never delete anything"

    def test_verified_good_trail_is_actually_deleted_with_execute(self, stores):
        receipt_store, _ = stores
        ref = receipt_store.create(b"jpeg-bytes", filename="r.jpg", media_type="image/jpeg")
        old_bin = _receipts_dir(stores) / f"{ref}.bin"
        old_bin.write_bytes(b"jpeg-bytes")

        results = script.run(execute=True)
        hit = next(r for r in results if r.kind == "receipt" and r.id == ref)

        assert hit.status == "deleted"
        assert not old_bin.exists()
        # The real replacement file is untouched.
        assert receipt_store.read_bytes(ref) == b"jpeg-bytes"

    def test_byte_mismatched_trail_is_skipped_not_deleted(self, stores):
        """The corrupted/mismatched case: the .bin trail does NOT match the real file's
        bytes (simulating a corrupt or hand-edited leftover) — must be skipped, in BOTH
        dry-run and --execute, never deleted."""
        receipt_store, _ = stores
        ref = receipt_store.create(b"real-jpeg-bytes", filename="r.jpg", media_type="image/jpeg")
        old_bin = _receipts_dir(stores) / f"{ref}.bin"
        old_bin.write_bytes(b"CORRUPTED-DIFFERENT-BYTES")

        dry_results = script.run(execute=False)
        dry_hit = next(r for r in dry_results if r.kind == "receipt" and r.id == ref)
        assert dry_hit.status == "skipped_verification_failed"
        assert old_bin.exists()

        exec_results = script.run(execute=True)
        exec_hit = next(r for r in exec_results if r.kind == "receipt" and r.id == ref)
        assert exec_hit.status == "skipped_verification_failed"
        assert old_bin.exists(), "a mismatched trail must survive --execute too"

    def test_no_trail_present_is_not_a_candidate(self, stores):
        receipt_store, _ = stores
        ref = receipt_store.create(b"bytes", filename="r.jpg", media_type="image/jpeg")

        results = script.run(execute=True)
        hit = next(r for r in results if r.kind == "receipt" and r.id == ref)

        assert hit.status == "skipped_no_trail"
        assert hit.bytes_reclaimed == 0

    def test_missing_replacement_file_is_skipped(self, stores):
        """The real extensioned file was deleted out-of-band (or never written) — the trail
        .bin must never be deleted since nothing proves the replacement actually works."""
        receipt_store, _ = stores
        ref = receipt_store.create(b"bytes", filename="r.jpg", media_type="image/jpeg")
        real_file = receipt_store.blob_path(ref)
        real_file.unlink()
        old_bin = _receipts_dir(stores) / f"{ref}.bin"
        old_bin.write_bytes(b"bytes")

        results = script.run(execute=True)
        hit = next(r for r in results if r.kind == "receipt" and r.id == ref)

        assert hit.status == "skipped_new_missing"
        assert old_bin.exists()

    def test_unknown_type_falling_back_to_bin_is_never_deleted_as_canonical(self, stores):
        """A receipt whose real extension genuinely IS `.bin` (unknown type) has no separate
        trail file at all — its one `.bin` file is the LIVE canonical file, not a superseded
        copy, and must never be treated as a deletable trail."""
        receipt_store, _ = stores
        ref = receipt_store.create(
            b"???", filename="mystery.xyz", media_type="application/x-mystery"
        )
        canonical = receipt_store.blob_path(ref)
        assert canonical.name == f"{ref}.bin"

        results = script.run(execute=True)
        hit = next(r for r in results if r.kind == "receipt" and r.id == ref)

        assert hit.status == "skipped_is_canonical"
        assert canonical.exists()


class TestAttachmentTrail:
    def _create_attachment(self, attachment_store, *, filename="bank.csv", media_type="text/csv"):
        entry = {"kind": "text", "text": "Date,Amount\n2026-01-01,-5.00", "format": "csv"}
        return attachment_store.create(
            entry,
            content=b"Date,Amount\n2026-01-01,-5.00",
            filename=filename,
            media_type=media_type,
        )

    def test_verified_good_backup_is_a_dry_run_candidate_not_deleted(self, stores):
        _, attachment_store = stores
        ref = self._create_attachment(attachment_store)
        raw_bytes = attachment_store.read_bytes(ref)
        import base64

        backup = _attachments_dir(stores) / f"{ref}.json.pre-raw-file-migration-20260101T000000Z"
        backup.write_text(
            json.dumps(
                {
                    "kind": "text",
                    "text": raw_bytes.decode(),
                    "format": "csv",
                    "content_b64": base64.b64encode(raw_bytes).decode(),
                }
            )
        )

        results = script.run(execute=False)
        hit = next(r for r in results if r.kind == "attachment" and r.id == ref)

        assert hit.status == "would_delete"
        assert hit.bytes_reclaimed == backup.stat().st_size
        assert backup.exists()

    def test_verified_good_backup_is_actually_deleted_with_execute(self, stores):
        _, attachment_store = stores
        ref = self._create_attachment(attachment_store)
        raw_bytes = attachment_store.read_bytes(ref)
        import base64

        backup = _attachments_dir(stores) / f"{ref}.json.pre-raw-file-migration-20260101T000000Z"
        backup.write_text(
            json.dumps(
                {
                    "kind": "text",
                    "text": raw_bytes.decode(),
                    "format": "csv",
                    "content_b64": base64.b64encode(raw_bytes).decode(),
                }
            )
        )

        results = script.run(execute=True)
        hit = next(r for r in results if r.kind == "attachment" and r.id == ref)

        assert hit.status == "deleted"
        assert not backup.exists()
        assert attachment_store.read_bytes(ref) == raw_bytes  # untouched

    def test_backup_with_mismatched_bytes_is_skipped_not_deleted(self, stores):
        """Corrupted/mismatched backup: its embedded content_b64 does not match the current
        raw file's bytes — must be skipped, in both dry-run and --execute."""
        _, attachment_store = stores
        ref = self._create_attachment(attachment_store)
        import base64

        backup = _attachments_dir(stores) / f"{ref}.json.pre-raw-file-migration-20260101T000000Z"
        backup.write_text(
            json.dumps(
                {
                    "kind": "text",
                    "text": "totally different content",
                    "format": "csv",
                    "content_b64": base64.b64encode(b"totally different content").decode(),
                }
            )
        )

        dry_hit = next(
            r for r in script.run(execute=False) if r.kind == "attachment" and r.id == ref
        )
        assert dry_hit.status == "skipped_verification_failed"
        assert backup.exists()

        exec_hit = next(
            r for r in script.run(execute=True) if r.kind == "attachment" and r.id == ref
        )
        assert exec_hit.status == "skipped_verification_failed"
        assert backup.exists()

    def test_corrupt_backup_json_is_skipped_not_deleted(self, stores):
        _, attachment_store = stores
        ref = self._create_attachment(attachment_store)
        backup = _attachments_dir(stores) / f"{ref}.json.pre-raw-file-migration-20260101T000000Z"
        backup.write_text("{not valid json")

        hit = next(r for r in script.run(execute=True) if r.kind == "attachment" and r.id == ref)

        assert hit.status == "skipped_verification_failed"
        assert backup.exists()

    def test_no_backup_present_is_not_a_candidate(self, stores):
        _, attachment_store = stores
        ref = self._create_attachment(attachment_store)

        hit = next(r for r in script.run(execute=True) if r.kind == "attachment" and r.id == ref)

        assert hit.status == "skipped_no_trail"
        assert hit.bytes_reclaimed == 0

    def test_missing_replacement_raw_file_is_skipped(self, stores):
        _, attachment_store = stores
        ref = self._create_attachment(attachment_store)
        raw_path = attachment_store.blob_path(ref)
        raw_path.unlink()
        backup = _attachments_dir(stores) / f"{ref}.json.pre-raw-file-migration-20260101T000000Z"
        backup.write_text(json.dumps({"kind": "text", "text": "x", "content_b64": "eA=="}))

        hit = next(r for r in script.run(execute=True) if r.kind == "attachment" and r.id == ref)

        assert hit.status == "skipped_new_missing"
        assert backup.exists()


class TestStatementParsedTrail:
    """Fix 8 (security review round 2): revision 0046's ``statements_parsed/`` trail —
    ``<id>.json.migrated-<ts>`` (superseded by the canonical ``files/statements/
    <id>.parsed.json`` sidecar) and ``<name>.removed-<ts>`` (an already-confirmed orphan)."""

    def _seed_migrated(self, statement_setup, rows):
        from backend.db.statement_store import StatementStore

        data_dir, _stmts_dir = statement_setup
        store = StatementStore()
        rec = store.create("a.csv", "text/csv", b"bytes", {"kind": "text"})
        store.set_parsed_rows(rec["id"], rows)
        old_parsed_dir = data_dir / "statements_parsed"
        old_parsed_dir.mkdir(parents=True, exist_ok=True)
        trail = old_parsed_dir / f"{rec['id']}.json.migrated-20260101T000000Z"
        return rec, old_parsed_dir, trail

    def test_verified_good_migrated_trail_is_a_dry_run_candidate_not_deleted(self, statement_setup):
        rows = [{"amount": 1.0}]
        rec, _old_dir, trail = self._seed_migrated(statement_setup, rows)
        trail.write_text(json.dumps(rows))

        results = script.run(execute=False)
        hit = next(r for r in results if r.kind == "statement_migrated" and r.id == rec["id"])

        assert hit.status == "would_delete"
        assert trail.exists(), "dry-run must never delete anything"

    def test_verified_good_migrated_trail_is_actually_deleted_with_execute(self, statement_setup):
        rows = [{"amount": 1.0}]
        rec, _old_dir, trail = self._seed_migrated(statement_setup, rows)
        trail.write_text(json.dumps(rows))

        results = script.run(execute=True)
        hit = next(r for r in results if r.kind == "statement_migrated" and r.id == rec["id"])

        assert hit.status == "deleted"
        assert not trail.exists()

    def test_json_mismatched_migrated_trail_is_skipped_not_deleted(self, statement_setup):
        rec, _old_dir, trail = self._seed_migrated(statement_setup, [{"amount": 1.0}])
        trail.write_text(json.dumps([{"amount": "CORRUPTED-DIFFERENT"}]))

        dry_hit = next(
            r
            for r in script.run(execute=False)
            if r.kind == "statement_migrated" and r.id == rec["id"]
        )
        assert dry_hit.status == "skipped_verification_failed"
        assert trail.exists()

        exec_hit = next(
            r
            for r in script.run(execute=True)
            if r.kind == "statement_migrated" and r.id == rec["id"]
        )
        assert exec_hit.status == "skipped_verification_failed"
        assert trail.exists(), "a mismatched trail must survive --execute too"

    def test_migrated_trail_with_no_current_row_is_skipped(self, statement_setup):
        data_dir, _stmts_dir = statement_setup
        old_parsed_dir = data_dir / "statements_parsed"
        old_parsed_dir.mkdir(parents=True)
        trail = old_parsed_dir / "stmt_nonexistent.json.migrated-20260101T000000Z"
        trail.write_text(json.dumps([{"amount": 1.0}]))

        results = script.run(execute=True)
        hit = next(
            r for r in results if r.kind == "statement_migrated" and r.id == "stmt_nonexistent"
        )

        assert hit.status == "skipped_new_missing"
        assert trail.exists()

    def test_removed_orphan_trail_dry_run_not_deleted(self, statement_setup):
        data_dir, _stmts_dir = statement_setup
        old_parsed_dir = data_dir / "statements_parsed"
        old_parsed_dir.mkdir(parents=True)
        orphan = old_parsed_dir / "stmt_orphan.json.removed-20260101T000000Z"
        orphan.write_text(json.dumps([{"amount": 1.0}]))

        results = script.run(execute=False)
        hit = next(r for r in results if r.kind == "statement_removed")

        assert hit.status == "would_delete"
        assert orphan.exists()

    def test_removed_orphan_trail_is_deleted_outright_with_execute(self, statement_setup):
        """A ``.removed-`` file carries no live counterpart to re-verify against BY
        DEFINITION — the migration itself already proved no row ever referenced it. It is
        eligible for deletion outright, unlike a ``.migrated-`` trail."""
        data_dir, _stmts_dir = statement_setup
        old_parsed_dir = data_dir / "statements_parsed"
        old_parsed_dir.mkdir(parents=True)
        orphan = old_parsed_dir / "stmt_orphan.json.removed-20260101T000000Z"
        orphan.write_text(json.dumps([{"amount": 1.0}]))

        results = script.run(execute=True)
        hit = next(r for r in results if r.kind == "statement_removed")

        assert hit.status == "deleted"
        assert not orphan.exists()

    def test_unrecognized_trail_filename_is_skipped(self, statement_setup):
        data_dir, _stmts_dir = statement_setup
        old_parsed_dir = data_dir / "statements_parsed"
        old_parsed_dir.mkdir(parents=True)
        stray = old_parsed_dir / "some_random_leftover_file.txt"
        stray.write_text("not a trail file")

        results = script.run(execute=True)
        hit = next(r for r in results if r.kind == "statement_trail")

        assert hit.status == "skipped_unrecognized_name"
        assert stray.exists()

    def test_missing_statements_parsed_dir_is_a_silent_noop(self, statement_setup):
        """A fresh install (or one that never had a parsed statement) has no
        ``statements_parsed/`` directory at all — must not crash, and contributes zero
        statement-trail results."""
        results = script.run(execute=True)
        assert not any(r.kind.startswith("statement") for r in results)


class TestNeverMutatesInDryRun:
    def test_dry_run_touches_no_files(self, stores):
        receipt_store, attachment_store = stores
        ref = receipt_store.create(b"jpeg-bytes", filename="r.jpg", media_type="image/jpeg")
        old_bin = _receipts_dir(stores) / f"{ref}.bin"
        old_bin.write_bytes(b"jpeg-bytes")

        script.run(execute=False)

        assert old_bin.exists()


class TestEndToEndViaMain:
    def test_dry_run_report_default(self, db_isolated, stores):
        rc = script.main(["--db-url", db_isolated])
        assert rc == 0

    def test_execute_flag_actually_deletes(self, stores):
        receipt_store, _ = stores
        ref = receipt_store.create(b"jpeg-bytes", filename="r.jpg", media_type="image/jpeg")
        old_bin = _receipts_dir(stores) / f"{ref}.bin"
        old_bin.write_bytes(b"jpeg-bytes")

        import backend.db.engine as eng

        db_url = eng.database_url()
        rc = script.main(["--db-url", db_url, "--execute"])
        assert rc == 0
        assert not old_bin.exists()

    def test_json_output(self, stores, capsys):
        receipt_store, _ = stores
        ref = receipt_store.create(b"bytes", filename="r.jpg", media_type="image/jpeg")
        old_bin = _receipts_dir(stores) / f"{ref}.bin"
        old_bin.write_bytes(b"bytes")

        import backend.db.engine as eng

        db_url = eng.database_url()
        rc = script.main(["--db-url", db_url, "--json"])
        assert rc == 0
        payload = json.loads(capsys.readouterr().out)
        hit = next(r for r in payload if r["kind"] == "receipt" and r["id"] == ref)
        assert hit["status"] == "would_delete"


class TestLiveDataGuard:
    def test_no_flags_refuses(self):
        with pytest.raises(SystemExit):
            script.main([])
