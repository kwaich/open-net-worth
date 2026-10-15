"""wave5f cutover: absorb SESSIONS_DIR's `{id}.extraction_cursor.json` sidecars into the
`sessions.extraction_cursor` column (built by revision 0034), with round-trip
verification. Wave 5F (JSON->DB, the zero-`.json` finisher).

Unlike every prior wave's absorb, a cursor file's `session_id` is NOT guaranteed to have
a matching `sessions` row — confirmed empirically against this migration's own real dev
data (307 real cursor files; 96 of them, ~31%, name a session that no longer has a row:
mostly `scheduled-*` automation-run sessions, whose id is minted fresh per run and never
reused, plus a handful of ordinary sessions that were deleted at some point — nothing
pre-migration ever cleaned up the cursor sidecar on session deletion). `session_io.
import_extraction_cursor` reports the miss instead of inventing a row:

  * a MATCH updates the row's `extraction_cursor` column and is stamped into
    `json_cutovers` exactly like every other wave's absorb — `_finish_cutovers` renames
    it `<name>.migrated-<ts>` only after the whole migration verifiably succeeds.
  * an ORPHAN cursor names data with no home. It is not "migrated" (nothing was written
    to the database for it) — renamed directly, right here, to `<name>.removed-<ts>`
    instead: the SAME non-destructive "rename, never delete" contract as a `.migrated-`
    absorb, but through the honest label for "confirmed dead, kept for manual recovery"
    (see revision 0038's own docstring for the sibling case this mirrors — a stale
    one-off sidecar with zero live readers). Done directly rather than via
    `json_cutovers` because that ledger's hash-reverify-then-rename contract exists to
    protect an ABSORBED file against being renamed out from under a write that landed
    after the absorb read it — there is no absorb here to protect. Idempotent by
    construction if this revision is retried after a partial failure: an already-renamed
    orphan no longer matches the `*.extraction_cursor.json` glob on the next run.

Revision ID: 0035
Revises: 0034
Create Date: 2026-08-10
"""

import hashlib
from datetime import datetime, timezone
from pathlib import Path

import sqlalchemy as sa
from alembic import op

revision = "202608110415_wave5f_cutover"
down_revision = "202608110414_wave5f_schema"
branch_labels = None
depends_on = None


def _stamp(conn, store: str, path: Path, raw: bytes, now: str) -> None:
    conn.execute(
        sa.text(
            "INSERT INTO json_cutovers (store, source_path, source_hash, migrated_at, renamed) "
            "VALUES (:s, :p, :h, :t, 0)"
        ),
        {"s": store, "p": str(path), "h": hashlib.sha256(raw).hexdigest(), "t": now},
    )


def _rename_removed(path: Path, stamp: str) -> None:
    """Non-destructively retire a confirmed-dead file: rename, never delete — the
    `.removed-<ts>` sibling of `_finish_cutovers`' own `.migrated-<ts>` rename in
    `backend/db/migrate.py`."""
    path.rename(path.with_name(f"{path.name}.removed-{stamp}"))


def upgrade() -> None:
    from backend.db import session_io
    from backend.db.alembic import versions_evidence_source as evsrc

    conn = op.get_bind()
    sessions_dir = evsrc.SESSIONS_SOURCE
    cursors, source_bytes = session_io.discover_extraction_cursors(sessions_dir)
    if not cursors:
        return  # fresh install, or no cursor sidecars at all: nothing to absorb

    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()
    now_stamp = now.strftime("%Y%m%dT%H%M%SZ")  # matches migrate.py's own _stamp() format

    matched: dict[str, dict] = {}
    orphaned: list[str] = []
    for session_id, doc in cursors.items():
        if session_io.import_extraction_cursor(conn, session_id, doc):
            matched[session_id] = doc
        else:
            orphaned.append(session_id)

    exported = session_io.export_all_extraction_cursors(conn)
    for session_id, doc in matched.items():
        if exported.get(session_id) != doc:
            raise RuntimeError(
                f"extraction-cursor cutover: round-trip verification FAILED for session "
                f"{session_id!r} — aborting the whole migration; the fail-closed wrapper "
                "will restore the pre-migrate backup"
            )

    for session_id in matched:
        filename = f"{session_id}.extraction_cursor.json"
        _stamp(
            conn,
            f"sessions_cursor:{filename}",
            sessions_dir / filename,
            source_bytes[filename],
            now_iso,
        )

    for session_id in orphaned:
        _rename_removed(sessions_dir / f"{session_id}.extraction_cursor.json", now_stamp)


def downgrade() -> None:
    """No-op: revision 0034 owns the `extraction_cursor` column and drops it when a
    caller downgrades past it, so this data-only revision's UPDATEs vanish with it —
    mirroring every other cutover's own downgrade. The `.removed-<ts>` orphan renames are
    NOT undone (matches `.migrated-<ts>`'s own precedent: a cutover's filesystem side
    effect is never automatically reversed by `downgrade()`)."""
