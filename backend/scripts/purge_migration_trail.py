"""Purge the migration-trail files revisions 0045 and 0046 deliberately left behind.

Revision 0045 (``backend/db/alembic/versions/202608220022_files_dir_and_raw_blob_storage.py``, see
``docs/adr/2026-08-21-unify-raw-file-storage-across-blob-stores.md``) brought receipts and
attachments in line with the rest of this app's blob stores — a real file, a real
extension — but deliberately left every ORIGINAL pre-migration file in place as a
non-destructive trail rather than deleting it:

  - ``RECEIPTS_DIR/<id>.bin`` — the pre-migration receipt blob, superseded by a
    byte-identical ``<id><ext>`` copy once ``ReceiptStore`` became extension-aware.
  - ``ATTACHMENTS_DIR/<id>.json.pre-raw-file-migration-<ts>`` — the pre-migration FULL
    attachment payload (raw bytes still base64-embedded), superseded by the new trimmed
    ``<id>.json`` + a real ``<id><ext>`` file.

Revision 0046 (``backend/db/alembic/versions/202608220859_derived_data_colocation.py``, see
``.claude/rfcs/2026-08-22-consolidate-derived-data-storage.md``) retired the separate
``DATA_DIR/statements_parsed/`` directory in favor of colocating a statement's derived
parsed-rows cache at ``files/statements/<id>.parsed.json`` — but, mirroring 0045's own
convention, left every file in the old directory in place as a non-destructive trail:

  - ``statements_parsed/<id>.json.migrated-<ts>`` — a row's full historical parsed-rows
    content, superseded by the new ``files/statements/<id>.parsed.json`` sidecar once that
    row was migrated.
  - ``statements_parsed/<name>.removed-<ts>`` — an ORPHAN: a file this migration's own step
    3 already proved no row's ``parsed_rows_path`` ever referenced (including a stale
    ``<id>.json.import-tmp-<hex>`` staged write left by an interrupted bundle import).

This tool is the deliberate follow-up cleanup those trails were left FOR — this repo's own
"never delete, rename/leave a trail once the new shape is confirmed correct, clean it up
later once it genuinely is" convention (Wave 5's own words). It is mechanical: every
candidate is independently RE-VERIFIED right before deletion, never trusting what an earlier
migration recorded — fresh sha256 byte-identity for a receipt, a real, successful round-trip
through the store's own ``read_payload()``/``read_bytes()`` methods for an attachment, and
fresh JSON-equality against the canonical sidecar for a ``.migrated-`` statement trail file. A
row whose replacement can't be freshly proven correct is skipped and logged, never deleted. A
``.removed-`` orphan carries no live counterpart to re-verify against BY DEFINITION (nothing
ever referenced it) and the whole ``statements_parsed/`` directory is permanently retired —
no code reads from it any more — so it is eligible for deletion outright, same as every other
already-confirmed-dead trail file this tool cleans up.

Defaults to ``--dry-run`` (reports only). Pass ``--execute`` to actually delete.

Usage:
    python3 -m backend.scripts.purge_migration_trail --db-url sqlite:////tmp/x/test.db
    python3 -m backend.scripts.purge_migration_trail --db-url sqlite:////tmp/x/test.db --execute
    python3 -m backend.scripts.purge_migration_trail --i-accept-live-data-risk --execute
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import logging
from dataclasses import asdict, dataclass

from backend.scripts import _live_data_guard

logger = logging.getLogger(__name__)

_STATEMENTS_PARSED_SUFFIX = ".json"

# Terminal statuses that mean "this row's trail file was (or would be) deleted".
_DELETE_STATUSES = frozenset({"deleted", "would_delete"})


@dataclass
class TrailResult:
    kind: str  # "receipt" | "attachment"
    id: str
    status: str
    paths: list[str]
    bytes_reclaimed: int
    detail: str


def _sha256(data: bytes) -> bytes:
    return hashlib.sha256(data).digest()


def _process_receipt(store, receipts_dir_module, row: dict, *, execute: bool) -> TrailResult:
    """One receipt row's worth of trail cleanup. Never trusts anything the migration
    itself recorded — the old ``.bin`` and the new extensioned file are both read FRESH
    here, and their bytes compared, right before (or instead of, in dry-run) deleting."""
    receipt_id = row["id"]
    old_path = receipts_dir_module.RECEIPTS_DIR / f"{receipt_id}.bin"
    new_path = store.blob_path(
        receipt_id, filename=row.get("filename"), media_type=row.get("media_type")
    )

    if not old_path.exists():
        return TrailResult("receipt", receipt_id, "skipped_no_trail", [str(old_path)], 0, "")

    if new_path == old_path:
        # The real extension IS .bin (a genuinely unknown type) — old_path is the ONLY
        # copy, the live canonical file, never a superseded trail. Must never delete it.
        return TrailResult(
            "receipt",
            receipt_id,
            "skipped_is_canonical",
            [str(old_path)],
            0,
            "real extension is .bin — this is the live file, not a trail copy",
        )

    new_bytes = store.read_bytes(receipt_id)  # exercises the store's real read path
    if new_bytes is None:
        return TrailResult(
            "receipt",
            receipt_id,
            "skipped_new_missing",
            [str(old_path), str(new_path)],
            0,
            "replacement file is not readable via ReceiptStore.read_bytes()",
        )

    old_bytes = old_path.read_bytes()
    if _sha256(old_bytes) != _sha256(new_bytes):
        return TrailResult(
            "receipt",
            receipt_id,
            "skipped_verification_failed",
            [str(old_path), str(new_path)],
            0,
            "fresh sha256 mismatch between .bin trail and its replacement — NOT deleting",
        )

    if execute:
        old_path.unlink(missing_ok=True)
        return TrailResult("receipt", receipt_id, "deleted", [str(old_path)], len(old_bytes), "")
    return TrailResult("receipt", receipt_id, "would_delete", [str(old_path)], len(old_bytes), "")


def _backup_expected_bytes(backup_json: dict) -> bytes | None:
    """The raw bytes a pre-migration attachment payload backup claims to carry — mirrors
    revision 0045's own ``_migrate_one_attachment_payload`` extraction exactly
    (``content_b64`` first, else the image ``data`` key). Returns ``None`` when the backup
    genuinely carries neither (the one kind ``attachment_parse.parse_upload`` never stashed
    raw bytes for pre-migration) — the caller then relies on the read-path proof alone
    instead of a byte comparison it has no evidence to make."""
    b64_source = backup_json.get("content_b64")
    if b64_source is None:
        b64_source = backup_json.get("data")
    if not isinstance(b64_source, str) or not b64_source:
        return None
    return base64.b64decode(b64_source, validate=True)


def _process_attachment(store, attachments_dir_module, row: dict, *, execute: bool) -> TrailResult:
    """One attachment row's worth of trail cleanup — mirrors ``_process_receipt``'s shape.
    A backup sidecar is only ever removed once the CURRENT (trimmed json + real-extension
    file) shape has been freshly proven to work through the store's own read methods, and
    — where the backup itself carries recoverable raw bytes — byte-identical to them."""
    attachment_id = row["id"]
    attachments_dir = attachments_dir_module.ATTACHMENTS_DIR
    backups = sorted(attachments_dir.glob(f"{attachment_id}.json.pre-raw-file-migration-*"))
    if not backups:
        return TrailResult("attachment", attachment_id, "skipped_no_trail", [], 0, "")

    payload = store.read_payload(attachment_id)
    if not isinstance(payload, dict):
        return TrailResult(
            "attachment",
            attachment_id,
            "skipped_new_missing",
            [str(p) for p in backups],
            0,
            "AttachmentStore.read_payload() did not return a payload",
        )

    raw_bytes = store.read_bytes(attachment_id)
    if raw_bytes is None:
        return TrailResult(
            "attachment",
            attachment_id,
            "skipped_new_missing",
            [str(p) for p in backups],
            0,
            "AttachmentStore.read_bytes() did not return bytes",
        )

    # Byte-identity against every backup found, where the backup carries recoverable bytes
    # at all — a backup missing content_b64/data (the legacy no-raw-bytes kind) can't be
    # compared, so it falls back to trusting the read-path proof above alone.
    for backup_path in backups:
        try:
            backup_json = json.loads(backup_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            return TrailResult(
                "attachment",
                attachment_id,
                "skipped_verification_failed",
                [str(p) for p in backups],
                0,
                f"backup sidecar {backup_path.name} unreadable/corrupt: {exc}",
            )
        if not isinstance(backup_json, dict):
            return TrailResult(
                "attachment",
                attachment_id,
                "skipped_verification_failed",
                [str(p) for p in backups],
                0,
                f"backup sidecar {backup_path.name} is not a JSON object",
            )
        try:
            expected = _backup_expected_bytes(backup_json)
        except (ValueError, TypeError) as exc:
            return TrailResult(
                "attachment",
                attachment_id,
                "skipped_verification_failed",
                [str(p) for p in backups],
                0,
                f"backup sidecar {backup_path.name} has corrupt base64: {exc}",
            )
        if expected is not None and _sha256(expected) != _sha256(raw_bytes):
            return TrailResult(
                "attachment",
                attachment_id,
                "skipped_verification_failed",
                [str(p) for p in backups],
                0,
                f"fresh sha256 mismatch between {backup_path.name} and the current raw "
                "file — NOT deleting",
            )

    total_bytes = sum(p.stat().st_size for p in backups)
    if execute:
        for backup_path in backups:
            backup_path.unlink(missing_ok=True)
        return TrailResult(
            "attachment", attachment_id, "deleted", [str(p) for p in backups], total_bytes, ""
        )
    return TrailResult(
        "attachment", attachment_id, "would_delete", [str(p) for p in backups], total_bytes, ""
    )


def _process_statement_migrated_trail(
    path, statement_store, statements_dir, *, execute: bool
) -> TrailResult:
    """One ``<id>.json.migrated-<ts>`` trail file — a row's full historical parsed-rows
    content, superseded by ``statements_files_dir()/<id>.parsed.json`` once that row was
    migrated by revision 0046. Re-verifies JSON-equality against the CURRENT canonical
    sidecar, freshly, right before deleting — never trusting the migration's own past
    verification, mirroring ``_process_receipt``/``_process_attachment``'s own reasoning."""
    base = path.name.split(".migrated-", 1)[0]
    if not base.endswith(_STATEMENTS_PARSED_SUFFIX):
        return TrailResult(
            "statement_migrated",
            path.name,
            "skipped_unrecognized_name",
            [str(path)],
            0,
            "trail filename does not match <id>.json.migrated-<ts>",
        )
    stmt_id = base[: -len(_STATEMENTS_PARSED_SUFFIX)]
    record = statement_store.find_by_id(
        stmt_id
    )  # the reinlined dict — has "parsed_rows", not the DB-only "has_parsed_rows" column
    if record is None or record.get("parsed_rows") is None:
        return TrailResult(
            "statement_migrated",
            stmt_id,
            "skipped_new_missing",
            [str(path)],
            0,
            "no current statement row with parsed rows for this id",
        )
    new_path = statements_dir / f"{stmt_id}.parsed.json"
    if not new_path.exists():
        return TrailResult(
            "statement_migrated",
            stmt_id,
            "skipped_new_missing",
            [str(path)],
            0,
            "canonical parsed-rows sidecar does not exist",
        )
    try:
        old_json = json.loads(path.read_text(encoding="utf-8"))
        new_json = json.loads(new_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return TrailResult(
            "statement_migrated",
            stmt_id,
            "skipped_verification_failed",
            [str(path)],
            0,
            f"trail file or its replacement is unreadable/corrupt: {exc}",
        )
    if old_json != new_json:
        return TrailResult(
            "statement_migrated",
            stmt_id,
            "skipped_verification_failed",
            [str(path)],
            0,
            "fresh JSON mismatch between .migrated- trail and its canonical replacement — "
            "NOT deleting",
        )
    size = path.stat().st_size
    if execute:
        path.unlink(missing_ok=True)
        return TrailResult("statement_migrated", stmt_id, "deleted", [str(path)], size, "")
    return TrailResult("statement_migrated", stmt_id, "would_delete", [str(path)], size, "")


def _process_statement_removed_trail(path, *, execute: bool) -> TrailResult:
    """One ``<name>.removed-<ts>`` orphan trail file — revision 0046's own step 3 already
    proved no row's ``parsed_rows_path`` ever referenced it, before it renamed the file this
    way. Unlike ``.migrated-``, there is no live counterpart to re-verify against BY
    DEFINITION (nothing was ever pointed at it), and the whole ``statements_parsed/``
    directory is permanently retired — no code reads from it any more — so it is eligible
    for deletion outright, same as every other already-confirmed-dead trail this tool
    cleans up."""
    size = path.stat().st_size
    detail = (
        "already-confirmed orphan — revision 0046's own step 3 established no row ever "
        "referenced this file before renaming it"
    )
    if execute:
        path.unlink(missing_ok=True)
        return TrailResult("statement_removed", path.name, "deleted", [str(path)], size, detail)
    return TrailResult("statement_removed", path.name, "would_delete", [str(path)], size, detail)


def run(*, execute: bool) -> list[TrailResult]:
    """Process every receipt row, attachment row, and statements_parsed/ trail file.
    Read-only unless ``execute=True``."""
    import backend.core.paths as paths_mod
    import backend.db.attachment_store as attachment_store_mod
    import backend.db.receipt_store as receipt_store_mod
    import backend.db.statement_store as statement_store_mod

    results: list[TrailResult] = []

    receipt_store = receipt_store_mod.ReceiptStore()
    for row in receipt_store.all():
        result = _process_receipt(receipt_store, receipt_store_mod, row, execute=execute)
        if result.status == "skipped_verification_failed":
            logger.warning("receipt %s: %s", result.id, result.detail)
        results.append(result)

    attachment_store = attachment_store_mod.AttachmentStore()
    for row in attachment_store.all():
        result = _process_attachment(attachment_store, attachment_store_mod, row, execute=execute)
        if result.status == "skipped_verification_failed":
            logger.warning("attachment %s: %s", result.id, result.detail)
        results.append(result)

    old_parsed_dir = paths_mod.DATA_DIR / "statements_parsed"
    if old_parsed_dir.is_dir():
        statement_store = statement_store_mod.StatementStore()
        statements_dir = paths_mod.statements_files_dir()
        for path in sorted(old_parsed_dir.iterdir()):
            if path.is_dir() or path.is_symlink():
                continue
            if ".migrated-" in path.name:
                result = _process_statement_migrated_trail(
                    path, statement_store, statements_dir, execute=execute
                )
            elif ".removed-" in path.name:
                result = _process_statement_removed_trail(path, execute=execute)
            else:
                result = TrailResult(
                    "statement_trail",
                    path.name,
                    "skipped_unrecognized_name",
                    [str(path)],
                    0,
                    "does not match a known migration-trail suffix",
                )
            if result.status == "skipped_verification_failed":
                logger.warning("%s %s: %s", result.kind, result.id, result.detail)
            results.append(result)

    return results


def _print_summary(results: list[TrailResult], *, execute: bool) -> None:
    by_status: dict[str, list[TrailResult]] = {}
    for r in results:
        by_status.setdefault(r.status, []).append(r)

    reclaimed = sum(r.bytes_reclaimed for r in results if r.status in _DELETE_STATUSES)
    verb = "Deleted" if execute else "Would delete"
    mode = "EXECUTE" if execute else "DRY RUN"

    print(f"purge_migration_trail — {mode}")
    print(f"  receipts scanned:        {sum(1 for r in results if r.kind == 'receipt')}")
    print(f"  attachments scanned:     {sum(1 for r in results if r.kind == 'attachment')}")
    print(
        "  statement trail files:  "
        f"{sum(1 for r in results if r.kind in ('statement_migrated', 'statement_removed', 'statement_trail'))}"
    )
    print()
    for status in sorted(by_status):
        print(f"  {status}: {len(by_status[status])}")
    print()
    print(
        f"{verb} {len(by_status.get('deleted', []) or by_status.get('would_delete', []))} "
        f"trail file(s), reclaiming {reclaimed:,} bytes."
    )

    skipped_bad = by_status.get("skipped_verification_failed", [])
    if skipped_bad:
        print()
        print(f"  {len(skipped_bad)} row(s) SKIPPED on failed verification (never deleted):")
        for r in skipped_bad:
            print(f"    {r.kind} {r.id}: {r.detail}")

    if not execute and (by_status.get("deleted") or by_status.get("would_delete")):
        print()
        print("  Dry run only — pass --execute to actually delete. Nothing was removed.")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    _live_data_guard.add_target_args(ap)
    ap.add_argument(
        "--execute",
        action="store_true",
        help="Actually delete verified trail files. Without this flag, only reports "
        "what WOULD be deleted (default: dry-run).",
    )
    ap.add_argument("--json", action="store_true", help="machine-readable report on stdout.")
    args = ap.parse_args(argv)

    db_url = _live_data_guard.resolve_db_url(args)
    _live_data_guard.bind_db_url(db_url)

    results = run(execute=args.execute)

    if args.json:
        print(json.dumps([asdict(r) for r in results], indent=2))
        return 0

    _print_summary(results, execute=args.execute)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
