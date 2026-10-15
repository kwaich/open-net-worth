"""profile cutover: absorb profile.json into the ``profile`` table (built by revision 0020), with
round-trip verification. Wave 5 (JSON->DB).

Mirrors 0010's ``_absorb_one``/``_stamp_cutover`` exactly, single store: read profile.json's bytes
ONCE (hash + parse the SAME bytes), import via ``profile_io.import_profile``, EXACT round-trip
verify (``export_profile(conn) == original``) — a diff RAISES, aborting inside Alembic's one
transaction, and the fail-closed ``ensure_migrated`` wrapper then restores the pre-migrate backup.
profile.json parses to a JSON object (dict); a non-dict root RAISES rather than silently dropping
data.

Only on success does a ``json_cutovers`` row land (source hash included), so ``_finish_cutovers``
renames profile.json -> ``profile.json.migrated-<ts>`` after ``upgrade head`` fully succeeds, ONLY
if the bytes still hash the same.

Revision ID: 0021
Revises: 0020
Create Date: 2026-08-10
"""

import hashlib
import json
from datetime import datetime, timezone

import sqlalchemy as sa
from alembic import op

revision = "202608110401_profile_cutover"
down_revision = "202608110400_profile_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    from backend.db import profile_io
    from backend.db.alembic import versions_evidence_source as evsrc

    conn = op.get_bind()
    src = evsrc.PROFILE_SOURCE
    if not src.exists():
        return  # fresh install: nothing to absorb for this store

    raw = src.read_bytes()  # ONE read: the hash below and the parse see identical bytes
    original = json.loads(raw.decode("utf-8"))
    if not isinstance(original, dict):
        raise RuntimeError(
            f"profile cutover: {src} is not a JSON object (dict expected at the root) — got "
            f"{type(original).__name__}; aborting rather than silently dropping data"
        )
    profile_io.import_profile(conn, original)
    if profile_io.export_profile(conn) != original:
        raise RuntimeError(
            "profile cutover: round-trip verification FAILED — aborting the migration; the "
            "fail-closed wrapper will restore the pre-migrate backup"
        )
    conn.execute(
        sa.text(
            "INSERT INTO json_cutovers (store, source_path, source_hash, migrated_at, renamed) "
            "VALUES (:s, :p, :h, :t, 0)"
        ),
        {
            "s": "profile",
            "p": str(src),
            "h": hashlib.sha256(raw).hexdigest(),  # _finish_cutovers refuses a drifted rename
            "t": datetime.now(timezone.utc).isoformat(),
        },
    )


def downgrade() -> None:
    """No-op: revision 0020 owns the profile DDL and drops the table when a caller downgrades past
    it, so this data-only revision's INSERT vanishes with the table. The renamed
    ``profile.json.migrated-<ts>`` source (once ``ensure_migrated()`` has run for real, not just a
    bare ``command.upgrade``) is the manual restore path — an automatic, data-destructive
    downgrade here would be a lie (0010's exact rationale)."""
