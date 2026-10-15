"""wave5e cutover: absorb SESSIONS_DIR into the `sessions` table (built by revision
0032), with round-trip verification. Wave 5E (JSON->DB, chat sessions).

Unlike every prior wave, the source is a whole DIRECTORY, not one JSON file:
`db.session_io.discover_sessions` reads it once — every `{id}.json` transcript, its
`{id}.fold.json`/`{id}.proactive.json` siblings, and the shared `index.json` metadata
list, cross-referencing BOTH directions of drift a live install's index/directory pair
can carry (a transcript with no index entry gets a synthesized meta; an index entry with
no transcript is preserved as an empty-transcript row) — see that function's own
docstring for the full reasoning, confirmed against this repo's own real ~62MB/1016-file
SESSIONS_DIR (679 sessions absorbed: 671 with a transcript file, 8 of those missing an
index entry and needing the synthesized-meta path; 25 more index-only orphans with no
transcript file at all).

Every discovered session is imported and EXACT round-trip verified
(`export_all_sessions(conn)[id] == the row that was read`) as one all-or-nothing unit — a
single bad transcript (or a non-list `index.json`) RAISES, aborting inside Alembic's one
transaction; the fail-closed `ensure_migrated` wrapper then restores the pre-migrate
backup.

`json_cutovers` STAMPING is per PHYSICAL FILE, not per session — `discover_sessions`
returns the raw bytes it actually read for every file touched (transcripts, fold/
proactive sidecars, and `index.json`, keyed by bare filename), so this revision stamps
and later renames each one exactly ONCE. This matters because `index.json` is SHARED by
every session's meta: stamping one `json_cutovers` row per session (rather than per file)
would produce many rows all pointing at the same `source_path`, and `_finish_cutovers`
(db/migrate.py) renames on the FIRST row it processes for a given path — every subsequent
row referencing an already-renamed path would silently no-op instead of erroring, which
works by accident rather than by design. Per-file stamping (`store` keyed
`"sessions:<filename>"`) avoids that fragility entirely: every `json_cutovers` row maps
to exactly one rename.

The whole SESSIONS_DIR directory is left in place after every contained file is renamed
to `<name>.migrated-<ts>` — unlike every other wave's single renamed source file, this
one is a shared directory: `ai/memory.py`'s own per-session `{id}.extraction_cursor.json`
state file lives in the SAME directory and is OUT OF SCOPE for this wave (a later call
decides file-vs-DB for it) — renaming or removing the directory itself would break it.

Revision ID: 0033
Revises: 0032
Create Date: 2026-08-10
"""

import hashlib
from datetime import datetime, timezone
from pathlib import Path

import sqlalchemy as sa
from alembic import op

revision = "202608110413_wave5e_cutover"
down_revision = "202608110412_wave5e_schema"
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


def upgrade() -> None:
    from backend.db import session_io
    from backend.db.alembic import versions_evidence_source as evsrc

    conn = op.get_bind()
    sessions_dir = evsrc.SESSIONS_SOURCE
    rows, source_bytes = session_io.discover_sessions(sessions_dir)
    if not source_bytes:
        return  # fresh install, or no SESSIONS_DIR contents at all: nothing to absorb

    for row in rows:
        session_io.import_session_row(conn, row)

    exported = {r["id"]: r for r in session_io.export_all_sessions(conn)}
    for row in rows:
        if exported.get(row["id"]) != row:
            raise RuntimeError(
                f"sessions cutover: round-trip verification FAILED for session "
                f"{row['id']!r} — aborting the whole migration (every session this "
                "revision absorbs is one all-or-nothing unit); the fail-closed wrapper "
                "will restore the pre-migrate backup"
            )

    now = datetime.now(timezone.utc).isoformat()
    for filename, raw in source_bytes.items():
        _stamp(conn, f"sessions:{filename}", sessions_dir / filename, raw, now)


def downgrade() -> None:
    """No-op: revision 0032 owns the `sessions` table DDL and drops it when a caller
    downgrades past it, so this data-only revision's INSERTs vanish with it — mirroring
    every other cutover's own downgrade (0010/0021/0025/0027/0029/0031's precedent: the
    renamed `<name>.migrated-<ts>` sources, once `ensure_migrated()` has run for real,
    are the manual restore path, not an automatic data-destructive downgrade)."""
