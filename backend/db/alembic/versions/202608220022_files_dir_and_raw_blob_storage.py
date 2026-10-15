"""Unify raw-file storage across the four blob stores + move them under one shared
``DATA_DIR/files/`` parent.

The owner found this by literally listing ``~/.open-net-worth/data/*/`` — three of the
four file-backed blob stores already do the right thing (a real file, a real extension,
safely derived from filename/media_type — never a caller-supplied filename used as a path
component): ``artifacts``, ``statements``. ``receipts`` almost does — a real file, but
ALWAYS ``<id>.bin`` regardless of actual type. ``attachments`` was the real problem: every
chat upload was a JSON file with the raw bytes base64-encoded and wrapped INSIDE it, mixed
together with derived/parsed data — which is why a non-image chat attachment could never
be downloaded again (only images had their bytes reachable in a servable shape) and why a
scanned-PDF-promoted-as-a-receipt bug existed (bytes buried inside a parsed-cache field
instead of being a first-class file).

See ``docs/adr/2026-08-21-unify-raw-file-storage-across-blob-stores.md`` for the full
decision. Schema-free (no table/column changes) — this is a pure DATA + FILE SHAPE
migration, run through Alembic anyway (not a standalone ``backend/scripts/`` tool) because
that is this repo's own established precedent for exactly this kind of change: revision
0041's ``_move_blob_dir`` merged ``uploads/`` -> ``attachments/`` inside a normal boot
migration, giving it ``db.migrate.ensure_migrated()``'s fail-closed, verified-backup-first
boot gate for free — a standalone script the owner would have to remember to run
separately gets none of that safety net.

Four independent sub-steps, composed in the order that makes each one operate on files
already in their final resting place:

  1. ``_merge_move_dir``: DATA_DIR/{artifacts,attachments,receipts,statements} -> the SAME
     leaf name under DATA_DIR/files/ — a generalization of 0041's own directory-merge
     helper (parametrized paths instead of hardcoded names), same non-destructive
     merge-never-clobber contract, same reason it must be a MERGE and not a guarded
     ``shutil.move``: ``backend.core.paths`` unconditionally ``mkdir(exist_ok=True)``s
     every one of the FOUR new destination directories at import time, and that module
     sits on this very migration's import path (``db.engine`` / ``alembic/env.py``) — so
     by the time ``upgrade()`` runs, every destination already exists, fresh and empty.
     ``STATEMENTS_PARSED_DIR`` deliberately stays OUTSIDE ``files/`` — it holds
     PARSED/DERIVED statement rows (extracted transaction data), not an original uploaded
     file, so it isn't one of "this store's raw files" in the sense ``files/`` groups.

  2. ``_fix_statement_file_paths``/``_fix_artifact_file_paths``: step 1 relocates the BYTES,
     but a pre-existing ``statements_meta``/``artifacts`` row's stored ``file_path`` still
     reads the OLD ``statements/<id>.<ext>``/``artifacts/<id>.<ext>`` DATA_DIR-relative
     form — unlike receipts/attachments, these two stores were already "do the right thing"
     (a real file, safely derived), so step 1 alone left them with a real file at a NEW
     location and a DB row still pointing at the OLD one. Every row whose stored path
     starts with the bare old prefix gets it rewritten to the ``files/...`` form, in BOTH
     ``statements_meta``'s promoted ``file_path`` column and the identical key inside its
     verbatim ``payload`` (the read path — ``StatementStore._full`` — reads OUT OF
     ``payload``, not the promoted column) and inside ``artifacts.payload`` (no promoted
     column there). ``_rewrite_old_relative_path`` makes this idempotent.

  3. Receipt blobs: every receipts row's ``<id>.bin`` (now living at
     ``DATA_DIR/files/receipts/<id>.bin`` after step 1) gets a byte-identical COPY at
     ``<id><ext>``, ``ext`` derived from that row's own ``filename``/``media_type`` via
     ``core.file_ext.file_ext``. The original ``<id>.bin`` is left in place, NEVER deleted
     — this repo's own convention (Wave 5's "renamed `.migrated-<ts>`", "this repo does not
     aggressively garbage-collect superseded files"): once ``ReceiptStore`` itself is
     extension-aware (this same change's other half, ``backend/db/receipt_store.py``), a
     leftover ``.bin`` is simply unreferenced by any read path — safe to leave as a trail.
     ``ReceiptStore.delete_receipt``/``AttachmentStore.delete_attachment`` glob-unlink every
     ``<id>.*`` sibling in their store dir (not just the canonical current path), so this
     trail — and the pre-raw-file-migration backup step 4 leaves below — is genuinely
     removable by an explicit user delete, not orphaned forever.

  4. Attachment payloads: every attachments row's ``<id>.json`` payload
     (``DATA_DIR/files/attachments/<id>.json`` after step 1) is in the PRE-2026-08-21
     shape — raw bytes base64-encoded INSIDE it (``content_b64`` for a text/structured/
     pdf-with-text/pdf_pages kind, plain ``data`` for an image — the exact shape
     ``backend/db/attachment_store.py``'s old ``_encode_payload``/``_decode_payload`` used).
     For each row: extract those bytes, write them to a NEW real file (``<id><ext>``, same
     safe derivation as step 3), back up the OLD full ``<id>.json`` to
     ``<id>.json.pre-raw-file-migration-<ts>`` (never destroyed, mirrors Wave 5F's
     ``*.pre-*.json`` one-off-script safety-copy convention), then overwrite ``<id>.json``
     with the TRIMMED shape (derived fields only — kind/summary/format/text/images/... —
     no content_b64/data). Every extraction is verified byte-identical (sha256 of the
     extracted bytes vs. a fresh read of the newly-written raw file) before the row is
     considered migrated; a mismatch raises ``_ByteVerificationFailed`` and fails the whole
     migration (this is a boot-time, fail-closed migration — ``db.migrate.ensure_migrated()``
     already took a verified DB backup before calling this, and no file here is ever
     destroyed, so a failure here is always safely re-driveable, never data loss). Every
     OTHER per-row failure (unreadable JSON, a non-dict payload, an image row with a null
     ``data``, malformed base64) is caught, logged, and counted in the row's report instead
     of halting every other row's migration and every future boot attempt — see
     ``_fix_attachment_payloads``'s own docstring.

Revision ID: 0045
Revises: 0044
Create Date: 2026-08-21
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

import sqlalchemy as sa
from alembic import op

revision = "202608220022_files_dir_and_raw_blob_storage"
down_revision = "202608201228_rename_signals_to_notification_log"
branch_labels = None
depends_on = None

logger = logging.getLogger(__name__)

_STORE_DIR_NAMES = ("artifacts", "attachments", "receipts", "statements")

_receipts = sa.table(
    "receipts",
    sa.column("id", sa.Text),
    sa.column("payload", sa.JSON),
)
_attachments = sa.table(
    "attachments",
    sa.column("id", sa.Text),
    sa.column("payload", sa.JSON),
)
_statements_meta = sa.table(
    "statements_meta",
    sa.column("id", sa.Text),
    sa.column("file_path", sa.Text),
    sa.column("payload", sa.JSON),
)
_artifacts = sa.table(
    "artifacts",
    sa.column("id", sa.Text),
    sa.column("payload", sa.JSON),
)


class _ByteVerificationFailed(RuntimeError):
    """Raised ONLY when a post-write sha256 check does not match — real, detected data
    corruption. This is the one failure any per-row error handling in this migration
    still lets propagate and halt the (already fail-closed, verified-backup-first) boot
    gate; every other per-row failure is caught, logged, and counted instead — see
    ``_fix_attachment_payloads``'s docstring."""


def _assert_contained(candidate: Path, store_dir: Path) -> Path:
    """Defense-in-depth: ``candidate`` must resolve to a child of ``store_dir``. Every id
    driving a path in this migration is either server-minted (``new_id("rcpt"/"att")``) or,
    for statements/artifacts, this migration's own bare id-prefix rewrite — none should ever
    be able to escape the store directory, but a hostile id smuggled in by a prior bundle
    restore (receipts/attachments are portable/BRIDGED) is cheap to rule out outright rather
    than trust."""
    resolved_dir = store_dir.resolve()
    resolved = candidate.resolve()
    if not resolved.is_relative_to(resolved_dir):
        raise RuntimeError(f"{candidate} resolves outside {resolved_dir} — refusing to use it")
    return candidate


def _payload(raw) -> dict:
    """Mirrors revision 0041's own ``_payload`` — the ``payload`` column decodes to a dict
    through SQLAlchemy's JSON type on both dialects, but a str is tolerated so this cannot
    depend on how a given driver decodes the column."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return {}
    return raw if isinstance(raw, dict) else {}


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _merge_move_dir(old_dir: Path, new_dir: Path) -> None:
    """MERGE ``old_dir``'s contents into ``new_dir`` — never a bare rename/move gated on
    the destination being absent. See the module docstring's step 1 for why: by the time
    this runs, ``new_dir`` already exists (freshly created, empty) because
    ``backend.core.paths`` unconditionally ``mkdir(exist_ok=True)``s it at import time, and
    this migration's own import path runs through that module. Moving file-by-file and
    skipping a name already present at the destination (never clobbering) is merge-safe
    regardless of which side got created first. Best-effort on a missing/absent source — a
    fresh install has no old top-level dir at all, and that must never fail the boot gate.

    ``os.path.lexists`` (not ``Path.exists``) drives the "already relocated" check — a
    DANGLING symlink at the destination reads as absent under ``exists()`` (it follows the
    link), which would let ``shutil.move`` proceed against a broken link in a confusing,
    platform-dependent way; ``lexists`` sees the link itself regardless of what it points
    to. A symlink in the SOURCE is skipped-and-reported rather than blindly relocated — this
    migration only ever expects real files here."""
    if not old_dir.is_dir() or old_dir == new_dir:
        return
    new_dir.mkdir(parents=True, exist_ok=True)
    for item in old_dir.iterdir():
        if item.is_symlink():
            logger.warning("skipping symlink in %s, not relocating: %s", old_dir, item)
            continue
        target = new_dir / item.name
        if os.path.lexists(target):
            continue  # already relocated (or a name collision) — never clobber
        shutil.move(str(item), str(target))
    if not any(old_dir.iterdir()):
        old_dir.rmdir()


def _move_store_dirs_into_files(data_dir: Path, files_dir: Path) -> None:
    for name in _STORE_DIR_NAMES:
        _merge_move_dir(data_dir / name, files_dir / name)


def _fix_receipt_extensions(receipts_dir: Path) -> dict:
    """Step 2 — see module docstring. Returns a small report dict for the migration log."""
    from backend.core.file_ext import file_ext
    from backend.core.io import atomic_write_bytes

    conn = op.get_bind()
    report = {"rows": 0, "copied": 0, "already_correct": 0, "no_blob": 0}
    for row in conn.execute(sa.select(_receipts.c.id, _receipts.c.payload)).fetchall():
        report["rows"] += 1
        payload = _payload(row.payload)
        old_path = _assert_contained(receipts_dir / f"{row.id}.bin", receipts_dir)
        ext = file_ext(payload.get("filename"), payload.get("media_type"))
        new_path = _assert_contained(receipts_dir / f"{row.id}{ext}", receipts_dir)
        if new_path == old_path:
            report["already_correct"] += 1
            continue
        if not old_path.exists():
            report["no_blob"] += 1
            continue
        if new_path.exists():
            report["already_correct"] += 1
            continue
        original = old_path.read_bytes()
        atomic_write_bytes(new_path, original)
        if hashlib.sha256(new_path.read_bytes()).digest() != hashlib.sha256(original).digest():
            raise _ByteVerificationFailed(
                f"receipt {row.id!r}: extension-fix copy did not verify byte-identical "
                f"({old_path} -> {new_path})"
            )
        report["copied"] += 1
    return report


def _rewrite_old_relative_path(value: object, dir_name: str) -> str | None:
    """The corrected ``files/<dir_name>/...`` form of a pre-migration ``<dir_name>/...``
    DATA_DIR-relative path, or ``None`` when ``value`` doesn't need rewriting — either
    because it's already the new ``files/<dir_name>/...`` shape (idempotent re-run, or a
    row created fresh after this migration already landed) or because it isn't the expected
    old shape at all (never touched — an unrecognised value is left alone rather than
    guessed at)."""
    if not isinstance(value, str) or not value:
        return None
    if value.startswith(f"files/{dir_name}/"):
        return None
    prefix = f"{dir_name}/"
    if value.startswith(prefix):
        return f"files/{value}"
    return None


def _fix_statement_file_paths() -> dict:
    """Fix 1 (statements half) — ``upgrade()`` moves ``DATA_DIR/statements`` to
    ``DATA_DIR/files/statements`` (step 1) but a pre-existing ``statements_meta`` row's
    stored ``file_path`` still reads the OLD ``statements/<id>.<ext>`` form — both the
    promoted ``file_path`` column AND the identical key inside the verbatim ``payload`` JSON
    (``StatementStore._full`` reads the record back OUT OF ``payload``, not the promoted
    column — see ``money_io._statement_row``). Left unfixed, ``StatementStore.
    read_file_bytes``/``delete_statement`` resolve a path that no longer exists: a read
    silently returns ``None``, and a delete unlinks nothing while still dropping the DB row
    — the real file is never removed, permanently orphaned and still riding every DR backup.
    Idempotent via ``_rewrite_old_relative_path``. Returns a small report dict."""
    conn = op.get_bind()
    report = {"rows": 0, "fixed": 0}
    rows = conn.execute(
        sa.select(_statements_meta.c.id, _statements_meta.c.file_path, _statements_meta.c.payload)
    ).fetchall()
    for row in rows:
        report["rows"] += 1
        new_path = _rewrite_old_relative_path(row.file_path, "statements")
        if new_path is None:
            continue
        payload = _payload(row.payload)
        payload["file_path"] = new_path
        conn.execute(
            sa.update(_statements_meta)
            .where(_statements_meta.c.id == row.id)
            .values(file_path=new_path, payload=payload)
        )
        report["fixed"] += 1
    return report


def _fix_artifact_file_paths() -> dict:
    """Fix 1 (artifacts half) — mirrors ``_fix_statement_file_paths``. ``artifacts`` is a
    plain ``payload_table`` (no promoted ``file_path`` column, everything lives in
    ``payload``), so only the JSON field needs rewriting. Left unfixed, ``ArtifactStore.
    read_text`` silently returns ``None`` for every artifact that existed before this
    migration ran."""
    conn = op.get_bind()
    report = {"rows": 0, "fixed": 0}
    rows = conn.execute(sa.select(_artifacts.c.id, _artifacts.c.payload)).fetchall()
    for row in rows:
        report["rows"] += 1
        payload = _payload(row.payload)
        new_path = _rewrite_old_relative_path(payload.get("file_path"), "artifacts")
        if new_path is None:
            continue
        payload["file_path"] = new_path
        conn.execute(sa.update(_artifacts).where(_artifacts.c.id == row.id).values(payload=payload))
        report["fixed"] += 1
    return report


def _migrate_one_attachment_payload(conn, row, attachments_dir: Path, stamp: str) -> str:
    """One attachment row's worth of step 3 — extracted out of ``_fix_attachment_payloads``
    so that function can wrap every row in its own try/except (see that function's
    docstring). Returns one of ``"no_payload_file"``/``"already_trimmed"``/``"migrated"`` —
    the caller tallies it into the report. Raises ``_ByteVerificationFailed`` on a real
    sha256 mismatch (must halt boot); any OTHER exception (bad JSON, a non-dict payload,
    malformed/missing base64) is the caller's problem to catch."""
    from backend.core.file_ext import file_ext
    from backend.core.io import atomic_write_bytes, atomic_write_text

    meta = _payload(row.payload)
    payload_path = _assert_contained(attachments_dir / f"{row.id}.json", attachments_dir)
    if not payload_path.exists():
        return "no_payload_file"
    entry = json.loads(payload_path.read_text(encoding="utf-8"))
    if not isinstance(entry, dict):
        raise ValueError(f"attachment payload is not a JSON object (got {type(entry).__name__})")
    content_b64 = entry.pop("content_b64", None)
    data_b64 = entry.get("data")  # kept in the entry; only content_b64 was ever a "wrapper" key
    if content_b64 is None and (entry.get("kind") != "image" or "data" not in entry):
        return "already_trimmed"

    b64_source = content_b64 if content_b64 is not None else data_b64
    if not isinstance(b64_source, str) or not b64_source:
        # Covers the "kind": "image", "data": null shape the skip-guard above does not
        # catch (kind IS "image" and "data" IS a present key, just a null one) — without
        # this check the fallback below is base64.b64decode(None), an uncaught TypeError.
        raise ValueError(f"attachment has no usable base64 bytes to extract (got {b64_source!r})")
    raw_bytes = base64.b64decode(b64_source, validate=True)

    ext = file_ext(meta.get("filename"), meta.get("media_type"))
    raw_path = _assert_contained(attachments_dir / f"{row.id}{ext}", attachments_dir)

    backup_path = payload_path.with_name(f"{payload_path.name}.pre-raw-file-migration-{stamp}")
    if not backup_path.exists():
        backup_path.write_text(payload_path.read_text(encoding="utf-8"), encoding="utf-8")

    if not raw_path.exists():
        atomic_write_bytes(raw_path, raw_bytes)
    if hashlib.sha256(raw_path.read_bytes()).digest() != hashlib.sha256(raw_bytes).digest():
        raise _ByteVerificationFailed(
            f"attachment {row.id!r}: raw-bytes extraction did not verify byte-identical "
            f"({raw_path})"
        )

    trimmed = {k: v for k, v in entry.items() if k not in ("content", "data")}
    # atomic_write_text (not a bare Path.write_text, unlike the pre-fix version of this
    # function) — a crash mid-write must never leave a truncated, permanently-unparseable
    # JSON file: this migration is ONE-TIME-transient-failure-recoverable everywhere else
    # (nothing is ever destroyed without a backup first), and a bare write_text here was the
    # one place that guarantee didn't actually hold.
    atomic_write_text(payload_path, json.dumps(trimmed, ensure_ascii=False))

    # The DB metadata row's own size_bytes was minted from the OLD base64+JSON-wrapped
    # payload size (services/attachment_store.py's pre-2026-08-21 create()) — leaving
    # it as-is after this migration would silently misreport every pre-existing
    # attachment's size in Settings -> Uploaded files forever. Correct it to the true
    # raw-file size, matching what a freshly-created row stores from now on.
    if meta.get("size_bytes") != len(raw_bytes):
        meta["size_bytes"] = len(raw_bytes)
        conn.execute(
            sa.update(_attachments).where(_attachments.c.id == row.id).values(payload=meta)
        )
    return "migrated"


def _fix_attachment_payloads(attachments_dir: Path) -> dict:
    """Step 3 — see module docstring. Returns a small report dict for the migration log.

    Fail-SAFE per row: ``ensure_migrated()`` calling this is a fail-CLOSED boot gate — an
    uncaught exception here would refuse to boot on EVERY future attempt, not just once,
    since nothing about one bad row changes between attempts. A malformed payload
    (unreadable JSON, a non-dict shape, an image row with ``"data": null``, corrupt base64)
    is therefore caught, logged, and counted in ``report["failed"]`` rather than allowed to
    halt every other row's migration — and the whole boot — forever. The ONE exception that
    still propagates and halts boot is ``_ByteVerificationFailed``: a sha256 mismatch means
    real data corruption was just detected, not a malformed-but-recoverable row, and this
    migration is not the place to silently paper over that."""
    conn = op.get_bind()
    stamp = _stamp()
    report: dict = {
        "rows": 0,
        "migrated": 0,
        "already_trimmed": 0,
        "no_payload_file": 0,
        "failed": [],
    }
    for row in conn.execute(sa.select(_attachments.c.id, _attachments.c.payload)).fetchall():
        report["rows"] += 1
        try:
            status = _migrate_one_attachment_payload(conn, row, attachments_dir, stamp)
        except _ByteVerificationFailed:
            raise
        except Exception as exc:
            logger.warning(
                "attachment %r: raw-file migration failed for this row, skipping it (%s: %s)",
                row.id,
                type(exc).__name__,
                exc,
            )
            report["failed"].append((row.id, f"{type(exc).__name__}: {exc}"))
            continue
        report[status] += 1
    return report


def upgrade() -> None:
    from backend.core.paths import DATA_DIR, FILES_DIR

    _move_store_dirs_into_files(DATA_DIR, FILES_DIR)
    _fix_statement_file_paths()
    _fix_artifact_file_paths()
    _fix_receipt_extensions(FILES_DIR / "receipts")
    _fix_attachment_payloads(FILES_DIR / "attachments")


def downgrade() -> None:
    """Structural only (mirrors revision 0041's own downgrade note): the four directories
    move back to being flat DATA_DIR siblings. The receipt extension-copies and the
    attachment payload trims/raw-file extractions from ``upgrade()`` are NOT reversed —
    every original file they touched was left in place (never deleted, never overwritten
    without a ``.pre-raw-file-migration-<ts>`` backup first), so nothing is lost, but
    stitching the pre-migration JSON shape back together from those backups is a manual
    recovery action, not something this downgrade attempts automatically."""
    from backend.core.paths import DATA_DIR, FILES_DIR

    for name in _STORE_DIR_NAMES:
        _merge_move_dir(FILES_DIR / name, DATA_DIR / name)
