"""Rename `uploads` -> `attachments`: the durable cross-channel AttachmentStore.

`.codex/rfcs/cross-channel-attachments-vision-and-statement-evidence.md` section 2.1 and
`docs/adr/2026-08-15-separate-attachments-receipts-and-statements.md` require every chat
channel (web upload AND Telegram photo/document) to persist an inbound file once, before
the turn, into ONE durable store carrying source/dedup provenance the old `UploadStore`
never had: `source_channel`, `source_conversation_id`, `source_message_id`, `content_hash`,
`media_type` (first-class, not buried inside an image-only payload field), and
`blob_locator` (an explicit record of the payload convention that was previously implicit
in `UPLOADS_DIR / f"{id}.json"`).

This is a clean RENAME (`backend/db/attachment_store.py::AttachmentStore` replaces
`backend/db/upload_store.py::UploadStore`; the AI tool surface — `get_uploads`/
`delete_upload` — is left named as-is, since a tool rename needs a persisted alias per
CLAUDE.md and nothing about the RFC requires renaming the tool), not a payload-only
extension: the `PayloadTableStore` shape is schemaless per-row JSON, so no column
migration is needed for the six new fields — only the table + its `.mkdir()`ed blob
directory move. See ADR `2026-08-16-attachment-store-replaces-upload-store` for the
rename-vs-extend decision and the fields added.

Existing rows predate every new field and cannot be reclassified with confidence (RFC
§"Migration, deletion, observability": "Never classify ambiguous records silently") — a
legacy row gets `source_channel="legacy:unknown"`, `content_hash=None`, `metadata={}`
rather than a guessed channel. Deeper migration diagnostics (item 31, dry-run receipts)
are explicitly Phase 6 scope, not this revision's job.

Revision ID: 0041
Revises: 0040
Create Date: 2026-08-16
"""

from __future__ import annotations

import json
import shutil

import sqlalchemy as sa
from alembic import op

revision = "202608161716_attachment_store"
down_revision = "202608140340_one_permission_store"
branch_labels = None
depends_on = None

_OLD_TABLE = "uploads"
_TABLE = "attachments"

_attachments = sa.table(
    _TABLE,
    sa.column("seq", sa.Integer),
    sa.column("id", sa.Text),
    sa.column("payload", sa.JSON),
)

# New minimum-record fields (RFC section 2.1) that every pre-existing row lacks. Values
# chosen so a legacy row is honest about what is NOT known, never a guess.
_NEW_FIELD_DEFAULTS = {
    "media_type": None,
    "content_hash": None,
    "blob_locator": None,  # AttachmentStore derives this from ATTACHMENTS_DIR + id at read
    "source_channel": "legacy:unknown",
    "source_message_id": None,
    "source_conversation_id": None,
    "metadata": {},
}


def _payload(raw) -> dict:
    """``payload`` comes back a dict through SQLAlchemy's JSON type on both dialects, but a
    str is tolerated so this cannot depend on how a given driver decodes the column."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return {}
    return raw if isinstance(raw, dict) else {}


def _backfill_new_fields(conn) -> None:
    """Every legacy row gains the six new keys with honest not-known defaults, so store
    code can read them uniformly instead of sprinkling ``.get(key, default)`` everywhere.
    Row-by-row for the same reason revision 0040 did this: the payload column's JSON
    dialect differs between SQLite and Postgres, and this table holds a handful to a few
    thousand rows — not a hot path worth cross-dialect JSON-patch SQL."""
    for row in conn.execute(sa.select(_attachments.c.id, _attachments.c.payload)).fetchall():
        payload = _payload(row.payload)
        missing = {k: v for k, v in _NEW_FIELD_DEFAULTS.items() if k not in payload}
        if not missing:
            continue
        conn.execute(
            sa.update(_attachments)
            .where(_attachments.c.id == row.id)
            .values(payload={**payload, **missing})
        )


def _move_blob_dir(old_name: str, new_name: str) -> None:
    """MERGE the payload blob directory (DATA_DIR/<old> -> DATA_DIR/<new>) — not a bare
    rename gated on the destination being absent.

    The row ids inside don't change (``{id}.json`` filenames are untouched), so relocating
    every file carries every payload. This must be a per-file merge, not
    ``shutil.move(old_dir, new_dir)`` behind a ``not new_dir.exists()`` guard: on the
    ``uploads`` -> ``attachments`` direction specifically, ``backend.core.paths`` module
    body unconditionally does ``ATTACHMENTS_DIR.mkdir(exist_ok=True)`` at IMPORT time, and
    that module sits on the import path of this very migration (via ``db.engine`` /
    ``alembic/env.py``) — so on every real boot (``db.migrate.ensure_migrated()``) or a
    standalone ``alembic upgrade head``, the fresh empty ``attachments/`` directory already
    exists by the time ``upgrade()`` runs. A guard of ``not new_dir.exists()`` is therefore
    always False and the move silently never fires, stranding every pre-existing
    attachment's payload file at the old path forever. Moving file-by-file and skipping a
    name already present at the destination (never clobbering) is merge-safe regardless of
    which side got created first. Best-effort on the source: a fresh install or a test DB
    has no ``old_dir`` at all, and that must never fail the migration (the fail-closed boot
    gate in ``db.migrate.ensure_migrated`` would refuse the whole boot over a directory
    that simply never existed)."""
    from backend.core.paths import DATA_DIR

    old_dir = DATA_DIR / old_name
    new_dir = DATA_DIR / new_name
    if not old_dir.is_dir() or old_dir == new_dir:
        return
    new_dir.mkdir(parents=True, exist_ok=True)
    for item in old_dir.iterdir():
        target = new_dir / item.name
        if target.exists():
            continue  # already relocated (or a name collision) — never clobber
        shutil.move(str(item), str(target))
    if not any(old_dir.iterdir()):
        old_dir.rmdir()


def upgrade() -> None:
    conn = op.get_bind()
    op.drop_index(f"ix_{_OLD_TABLE}_seq", table_name=_OLD_TABLE)
    op.rename_table(_OLD_TABLE, _TABLE)
    op.create_index(f"ix_{_TABLE}_seq", _TABLE, ["seq"], unique=True)
    _backfill_new_fields(conn)
    _move_blob_dir("uploads", "attachments")


def downgrade() -> None:
    """Structural only — the six new fields are left on the rows (harmless extra keys
    under the old name; ``UploadStore``'s payload dict never validated against a fixed
    schema). The blob directory is renamed back so old code keeps finding its files."""
    op.drop_index(f"ix_{_TABLE}_seq", table_name=_TABLE)
    op.rename_table(_TABLE, _OLD_TABLE)
    op.create_index(f"ix_{_OLD_TABLE}_seq", _OLD_TABLE, ["seq"], unique=True)
    _move_blob_dir("attachments", "uploads")
