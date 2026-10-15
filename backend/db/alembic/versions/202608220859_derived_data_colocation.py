"""Consolidate derived-data storage: one directory, one glob-delete, per blob store.

.claude/rfcs/2026-08-22-consolidate-derived-data-storage.md (Option A, recommended) states
the rule: **a blob store owns exactly ONE directory, ``files/<store>/`` — everything it keeps
on disk for id X lives there and starts with X: ``X<ext>`` is the original the user gave us,
``X.parsed.json`` is what we computed from it.** Two stores broke that rule before this
revision:

  - ``statements`` kept its derived cache in a SEPARATE top-level directory,
    ``DATA_DIR/statements_parsed/<id>.json`` — not even under ``files/``.
  - ``attachments`` already colocated its derived cache correctly (same directory, same id)
    but named it bare ``<id>.json``, not ``<id>.parsed.json`` — a latent collision hazard
    (RFC §3, item 4): today safe only because ``.json`` isn't in ``core/file_ext.py``'s
    extension allowlist, so a raw upload can never write over it; adding ``.json`` there
    later would let a raw file overwrite its own derived sidecar.

Both converge here. See ``docs/adr/2026-08-21-unify-raw-file-storage-across-blob-stores.md``'s
amendment for why the RFC revisits that ADR's own "STATEMENTS_PARSED_DIR deliberately stays
OUTSIDE files/" line, and this revision's own task report for the resolved design question
below.

DESIGN DEVIATION FROM THE RFC's OWN §5 PLAN (the RFC explicitly left this open, "resolve this
yourself" — not silently deviating from a stated RFC decision): ``statements_meta.
parsed_rows_path`` — a full, DATA_DIR-relative STORED path — is replaced by
``has_parsed_rows`` (a boolean marker), not merely rewritten to a new literal string as the
RFC's own §5/§4.3 prose assumed ("only to the two places that MINT the path... and to the
stored column VALUES" — no column-shape change). Once the destination is
``files/statements/<id>.parsed.json`` — always, deterministically, for every id — a full path
column stores nothing a marker doesn't already imply. The ONE property worth preserving
(``StatementStore.all()``/``query()`` skip a filesystem read entirely for the majority of
statements that were never parsed, rather than probing a file that provably doesn't exist —
confirmed by reading ``money_io._reinline_statement``'s own ``if parsed_rows_path:`` guard)
is served exactly as well by a boolean as by a path string, so the schema SHRINKS a column
rather than keeping a now-redundant one. ``attachments`` gets no equivalent column: its
sidecar is unconditionally written in the SAME ``create()`` call as the metadata row, so
"has this row's derived cache been written" is always true by construction there — a marker
would be redundant, not merely lower-value. This is a genuine schema change (unlike revision
0045, which was schema-free) — ``op.batch_alter_table`` is used for the drop+constraint step
so SQLite's lack of native ALTER-TABLE-DROP-COLUMN-and-ADD-CONSTRAINT recreates the table
safely (no FK references ``statements_meta``, so no cascade-on-recreate hazard, unlike
revision 0008's ``transactions`` table).

Four steps, mirroring revision 0045's own non-destructive, fault-tolerant, per-row shape:

  1. Schema: ``statements_meta`` gains ``has_parsed_rows`` (Integer 0/1, NOT NULL, default 0)
     — added FIRST, before any data moves, so both the old and new columns are readable/
     writable side by side during step 2.
  2. Statements data: for every row whose (still-present) ``parsed_rows_path`` is non-null,
     read the old file at ``DATA_DIR/<value>``, write it to
     ``FILES_DIR/statements/<id>.parsed.json``, VERIFY (sha256 byte-identity AND
     ``json.loads`` equality — the payload's failure mode is silent, see
     ``money_io._reinline_statement``'s own tolerant-degrade-to-None behavior), set
     ``has_parsed_rows=1``, rename the old file ``<id>.json.migrated-<ts>`` (never deleted,
     left in the old ``statements_parsed/`` directory for manual recovery).
  3. Statements orphans: any file left in the old ``statements_parsed/`` directory that no
     row's (pre-migration) ``parsed_rows_path`` ever referenced — including a stale
     ``<id>.json.import-tmp-<hex>`` staged write from an interrupted bundle import — is
     renamed ``<name>.removed-<ts>``. A file a row DOES still reference, whose own migration
     failed for a caught (non-corruption) reason, is deliberately left alone here — it is
     "still referenced, migration incomplete", not orphaned.
  4. Attachments data: for every row whose ``files/attachments/<id>.json`` exists and whose
     ``<id>.parsed.json`` does not, copy → verify (byte + JSON equality) → rename the old to
     ``<id>.json.migrated-<ts>``. ``AttachmentStore.unlink_all_blobs``'s existing ``<id>.*``
     glob already covers both the new name and this trail — no store-side change needed for
     that.
  5. Schema: drop ``parsed_rows_path``, add the ``has_parsed_rows IN (0,1)`` CheckConstraint
     (batched together — see the deviation note above).

Error policy mirrors revision 0045 exactly: every row is wrapped individually; a malformed
row (unreadable/non-JSON source, an id that resolves outside its store directory) is logged
and counted in that step's report, never allowed to halt every other row's migration and the
whole boot gate forever. The ONE exception that still propagates and halts boot is
``_VerificationFailed`` — this is a boot-time, fail-closed migration
(``db.migrate.ensure_migrated()`` already took a verified backup before calling this), and
nothing here is ever destroyed, so a failure is always safely re-driveable, never data loss.

Revision ID: 0046
Revises: 0045
Create Date: 2026-08-22
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import sqlalchemy as sa
from alembic import op
from sqlalchemy.schema import conv

revision = "202608220859_derived_data_colocation"
down_revision = "202608220022_files_dir_and_raw_blob_storage"
branch_labels = None
depends_on = None

logger = logging.getLogger(__name__)

_statements_meta = sa.table(
    "statements_meta",
    sa.column("id", sa.Text),
    sa.column("parsed_rows_path", sa.Text),
    sa.column("has_parsed_rows", sa.Integer),
)
_attachments = sa.table("attachments", sa.column("id", sa.Text))

# `conv(...)` — NOT a plain string — mirrors `backend/db/tables.py`'s own
# `CheckConstraint(..., name=conv("ck_statements_meta_has_parsed_rows_bool"))` exactly.
# Without it, `batch_op.create_check_constraint` treats the string as an unresolved
# "constraint_name" template variable and re-applies `NAMING_CONVENTION`'s
# `"ck": "ck_%(table_name)s_%(constraint_name)s"` a SECOND time, producing
# `ck_statements_meta_ck_statements_meta_has_parsed_rows_bool` — a name `tables.py`'s own
# metadata does not recognize, which is exactly what `alembic check`/`command.check()`
# (a real DDL-vs-metadata diff, not just a lint) caught: found via
# `tests/db/test_migrations.py::test_alembic_check_is_clean`.
_HAS_PARSED_ROWS_CK = conv("ck_statements_meta_has_parsed_rows_bool")


class _VerificationFailed(RuntimeError):
    """Raised ONLY when a post-write sha256/JSON-equality check does not match what was just
    read into memory before the write — real, detected data corruption. The one failure any
    per-row error handling in this migration still lets propagate and halt the (already
    fail-closed, verified-backup-first) boot gate; every other per-row failure (a malformed/
    non-JSON source file, an id resolving outside its store directory) is caught, logged, and
    counted instead — see each ``_migrate_one_*`` function's caller."""


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _assert_contained(candidate: Path, store_dir: Path) -> Path:
    """Defense-in-depth, mirrors revision 0045's own helper: ``candidate`` must resolve to a
    child of ``store_dir``. Every id driving a path here is either server-minted or, for a
    pre-existing statement, this migration's own bare id read from the DB — none should ever
    be able to escape the store directory, but a hostile id smuggled in by a prior bundle
    restore (both stores are portable/BRIDGED) is cheap to rule out outright rather than
    trust."""
    resolved_dir = store_dir.resolve()
    resolved = candidate.resolve()
    if not resolved.is_relative_to(resolved_dir):
        raise RuntimeError(f"{candidate} resolves outside {resolved_dir} — refusing to use it")
    return candidate


def _verify_copy(old_bytes: bytes, new_path: Path, *, label: str) -> None:
    """Re-reads ``new_path`` fresh and checks it against ``old_bytes`` — the exact bytes read
    into memory before the write, never a re-read of the OLD file (which this migration is
    about to rename out from under itself). Byte-identity is the strong check; JSON-equality
    is the one that means something to a human reading the migration report — both,
    deliberately, mirroring revision 0045's own dual-check reasoning for exactly this class of
    silent-failure-mode payload."""
    new_bytes = new_path.read_bytes()
    if hashlib.sha256(new_bytes).digest() != hashlib.sha256(old_bytes).digest():
        raise _VerificationFailed(f"{label}: copy did not verify byte-identical (-> {new_path})")
    if json.loads(new_bytes) != json.loads(old_bytes):
        raise _VerificationFailed(
            f"{label}: copy is byte-identical but JSON-unequal (should be impossible) "
            f"(-> {new_path})"
        )


def _migrate_one_statement_row(
    conn, row, statements_dir: Path, data_dir: Path, old_parsed_dir: Path, stamp: str
) -> str:
    """One statement row's worth of step 2. Returns one of ``"never_parsed"`` /
    ``"already_migrated"`` / ``"no_source_file"`` / ``"migrated"`` — the caller tallies it.
    Raises ``_VerificationFailed`` on a real post-write mismatch (must halt boot); any OTHER
    exception (malformed/non-JSON source, a contained-path violation on EITHER the new or the
    old path) is the caller's problem to catch — landed in that row's own ``report["failed"]``
    entry, never allowed to halt every other row's migration (security review fix round 2,
    Fix 1).

    ``row.parsed_rows_path`` is a DB STRING, not a server-minted id — a value that survived a
    portable-bundle restore is exactly as untrusted as any other bundle-sourced string (the
    FR-C1 threat model this repo already has for ``statement_id`` applies here at least as
    much, not less: this is the OLD path being READ from and then renamed away, not a fresh
    id building a new one). ``old_path`` is therefore re-proven contained in
    ``old_parsed_dir`` (the legacy ``statements_parsed/`` directory) exactly like ``new_path``
    is proven contained in ``statements_dir`` below — never trusted bare.

    CRASH-THEN-RETRY SAFETY: ``upgrade()`` runs inside ONE database transaction (env.py's
    ``context.begin_transaction()``), but the FILE writes/renames below are NOT part of that
    transaction and do not roll back with it. If a LATER row in this same run raises and
    aborts the whole revision, every row already migrated before it keeps its real
    ``<id>.parsed.json`` file on disk while its ``has_parsed_rows=1`` UPDATE is rolled back
    with everything else — so a retry must find ``new_path`` already present (the
    ``already_migrated`` branch) and still (re-)issue the UPDATE, never skip it, or that row
    would silently end up with a real file but ``has_parsed_rows=0`` forever."""
    from backend.core.io import atomic_write_text

    if not row.parsed_rows_path:
        return "never_parsed"
    new_path = _assert_contained(statements_dir / f"{row.id}.parsed.json", statements_dir)
    if new_path.exists():
        conn.execute(
            sa.update(_statements_meta)
            .where(_statements_meta.c.id == row.id)
            .values(has_parsed_rows=1)
        )
        return "already_migrated"
    old_path = _assert_contained(data_dir / row.parsed_rows_path, old_parsed_dir)
    if not old_path.exists():
        return "no_source_file"
    old_bytes = old_path.read_bytes()
    json.loads(old_bytes)  # malformed source -> raises here, caught by the caller as a failure
    atomic_write_text(new_path, old_bytes.decode("utf-8"), mode=0o600)
    _verify_copy(old_bytes, new_path, label=f"statement {row.id!r}")
    conn.execute(
        sa.update(_statements_meta).where(_statements_meta.c.id == row.id).values(has_parsed_rows=1)
    )
    old_path.rename(old_path.with_name(f"{old_path.name}.migrated-{stamp}"))
    return "migrated"


def _migrate_statements_rows(
    statements_dir: Path, data_dir: Path, old_parsed_dir: Path, stamp: str
) -> tuple[dict, set]:
    """Step 2 — see module docstring. Returns (report, referenced_names): ``referenced_names``
    is every OLD file basename any row's ``parsed_rows_path`` pointed at, pre-migration —
    step 3 (orphans) needs this snapshot to tell "no row ever referenced this file" apart from
    "a row references it but that row's own migration failed"."""
    conn = op.get_bind()
    rows = conn.execute(
        sa.select(_statements_meta.c.id, _statements_meta.c.parsed_rows_path)
    ).fetchall()
    referenced_names = {Path(r.parsed_rows_path).name for r in rows if r.parsed_rows_path}
    report: dict = {
        "rows": 0,
        "migrated": 0,
        "already_migrated": 0,
        "never_parsed": 0,
        "no_source_file": 0,
        "failed": [],
    }
    for row in rows:
        report["rows"] += 1
        try:
            status = _migrate_one_statement_row(
                conn, row, statements_dir, data_dir, old_parsed_dir, stamp
            )
        except _VerificationFailed:
            raise
        except Exception as exc:
            logger.warning(
                "statement %r: parsed-rows migration failed for this row, skipping it (%s: %s)",
                row.id,
                type(exc).__name__,
                exc,
            )
            report["failed"].append((row.id, f"{type(exc).__name__}: {exc}"))
            continue
        report[status] += 1
    return report, referenced_names


def _migrate_statements_orphans(old_parsed_dir: Path, referenced_names: set, stamp: str) -> dict:
    """Step 3 — see module docstring. Best-effort on a missing old directory (a fresh install,
    or one that never had a parsed statement, has no ``statements_parsed/`` at all)."""
    report = {"removed": 0}
    if not old_parsed_dir.is_dir():
        return report
    for item in sorted(old_parsed_dir.iterdir()):
        if item.is_dir():
            continue
        if item.is_symlink():
            logger.warning("skipping symlink in %s, not touching it: %s", old_parsed_dir, item)
            continue
        if ".migrated-" in item.name or ".removed-" in item.name:
            continue  # already a trail marker (this run's own step 2, or a prior run's)
        if item.name in referenced_names:
            continue  # a row still points at this — leave it for that row's own retry
        item.rename(item.with_name(f"{item.name}.removed-{stamp}"))
        report["removed"] += 1
    return report


def _migrate_one_attachment_payload(row, attachments_dir: Path, stamp: str) -> str:
    """One attachment row's worth of step 4. Returns one of ``"already_migrated"`` /
    ``"no_payload_file"`` / ``"migrated"``. Unlike revision 0045's own attachment step, the
    source here is ALREADY the post-2026-08-21 trimmed derived-fields shape (plain JSON, no
    base64 raw bytes to extract) — a straight copy+verify+rename."""
    from backend.core.io import atomic_write_text

    new_path = _assert_contained(attachments_dir / f"{row.id}.parsed.json", attachments_dir)
    if new_path.exists():
        return "already_migrated"
    old_path = _assert_contained(attachments_dir / f"{row.id}.json", attachments_dir)
    if not old_path.exists():
        return "no_payload_file"
    old_bytes = old_path.read_bytes()
    json.loads(old_bytes)  # malformed payload -> raises here, caught by the caller as a failure
    atomic_write_text(new_path, old_bytes.decode("utf-8"))
    _verify_copy(old_bytes, new_path, label=f"attachment {row.id!r}")
    old_path.rename(old_path.with_name(f"{old_path.name}.migrated-{stamp}"))
    return "migrated"


def _migrate_attachments_payloads(attachments_dir: Path, stamp: str) -> dict:
    """Step 4 — see module docstring. Fail-SAFE per row, same reasoning as
    ``_migrate_statements_rows``."""
    conn = op.get_bind()
    report: dict = {
        "rows": 0,
        "migrated": 0,
        "already_migrated": 0,
        "no_payload_file": 0,
        "failed": [],
    }
    for row in conn.execute(sa.select(_attachments.c.id)).fetchall():
        report["rows"] += 1
        try:
            status = _migrate_one_attachment_payload(row, attachments_dir, stamp)
        except _VerificationFailed:
            raise
        except Exception as exc:
            logger.warning(
                "attachment %r: derived-payload migration failed for this row, skipping it "
                "(%s: %s)",
                row.id,
                type(exc).__name__,
                exc,
            )
            report["failed"].append((row.id, f"{type(exc).__name__}: {exc}"))
            continue
        report[status] += 1
    return report


def upgrade() -> None:
    from backend.core.paths import DATA_DIR, FILES_DIR, statements_files_dir

    # statements_dir resolves via `core.paths.statements_files_dir()` — the ONE accessor
    # function every consumer of this directory calls (security review fix round 2, Fix 2 +
    # Fix 3: this migration used to import the module-level `STATEMENTS_FILES_DIR` constant
    # via `backend.db.money_io`, a re-export of `core.paths`'s own binding rather than the
    # OWNER itself — a future rename/drop of that re-export would raise `ImportError` INSIDE
    # `ensure_migrated()`, the fail-closed BOOT gate, refusing every install not yet past this
    # revision). Calling the accessor instead of importing either module's constant is also
    # what makes this migration see a test's isolation patch correctly: the test suite's
    # autouse `_isolate_statement_files` fixture (tests/conftest.py) now patches ONLY
    # `core.paths.STATEMENTS_FILES_DIR` (Fix 3 consolidated every other binding away), and the
    # accessor reads that same module global fresh on every call — resolved at CALL time, not
    # at whatever moment some other module first imported it. A fresh `FILES_DIR / "statements"`
    # join here would miss that patch entirely and write this migration's NEW sidecar into the
    # real (untouched) directory while every live read after it (`StatementStore`/
    # `money_io._reinline_statement`, which DO call the same accessor) looks in the throwaway
    # one — reproduced by
    # `tests/db/test_money_cutover.py::test_full_migration_absorbs_all_six_and_renames` running
    # a from-scratch cutover (revision 0007's amendment writes a statement's parsed-rows file,
    # this revision immediately moves it in the SAME chain run). `attachments_dir` has no such
    # split — nothing in the suite isolates `ATTACHMENTS_DIR` globally, so the plain join stays
    # correct.
    statements_dir = statements_files_dir()

    attachments_dir = FILES_DIR / "attachments"
    old_parsed_dir = DATA_DIR / "statements_parsed"
    stamp = _stamp()

    op.add_column(
        "statements_meta",
        sa.Column("has_parsed_rows", sa.Integer, nullable=False, server_default=sa.text("0")),
    )

    stmt_report, referenced_names = _migrate_statements_rows(
        statements_dir, DATA_DIR, old_parsed_dir, stamp
    )
    orphan_report = _migrate_statements_orphans(old_parsed_dir, referenced_names, stamp)
    att_report = _migrate_attachments_payloads(attachments_dir, stamp)
    logger.info(
        "revision 0046 derived-data colocation: statements=%s orphans=%s attachments=%s",
        stmt_report,
        orphan_report,
        att_report,
    )

    with op.batch_alter_table("statements_meta") as batch_op:
        batch_op.drop_column("parsed_rows_path")
        batch_op.create_check_constraint(_HAS_PARSED_ROWS_CK, "has_parsed_rows IN (0,1)")


def downgrade() -> None:
    """Structural only (mirrors revision 0045's own downgrade note): ``parsed_rows_path``
    comes back as a nullable column, always NULL for every row — reconstructing which rows
    HAD one (and stitching the pre-migration path string back together from the deterministic
    new location) is a manual recovery action, not something this downgrade attempts
    automatically. Nothing this revision's ``upgrade()`` did is ever destroyed (every renamed
    file is a `.migrated-`/`.removed-` trail, never deleted), so recovery is always possible,
    just not automatic here — identical trade-off to 0045's own downgrade."""
    with op.batch_alter_table("statements_meta") as batch_op:
        batch_op.drop_constraint(_HAS_PARSED_ROWS_CK, type_="check")
        batch_op.drop_column("has_parsed_rows")
        batch_op.add_column(sa.Column("parsed_rows_path", sa.Text, nullable=True))
