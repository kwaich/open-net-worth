"""misc cutover: absorb the 4 misc JSON sources (artifacts, uploads, email_snapshots,
pending_pushes) into their revision-0017 tables (plan 4e, task 1). Mirrors 0016 (configs
cutover)/0014 (logs cutover) exactly: read every present source's bytes ONCE, import,
EXACT round-trip verify (export == original), aborting the WHOLE migration on any diff.
No cross-file consistency check — no FKs between these 4 tables (or to any other table):
none is book/money-scoped.

SCOPE: this revision absorbs artifacts.json/uploads.json/email_snapshots.json/
pending_pushes.json ONLY. ``checkpoints`` (the 5th Wave-E table, schema-only from
revision 0017) is absorbed separately in a dedicated revision 0019 (plan 4e task 2) —
not this one; its store class doesn't exist yet.

All four sources are flat lists, already append-order on disk — none is a prepend/ring
source (see misc_io.py's own module docstring), so every job below uses the plain
default (no reverse flag, unlike 0016's ``signals``/0014's four prepend-order stores).
The round-trip check is still ORDER-SENSITIVE (plain ``==`` on the parsed JSON list is
positional/element-by-element), so a wrong assumption about append-vs-prepend order
would still fail loud here rather than silently pass on a same-shape fixture.

Only once every present store's import + round-trip verification has succeeded does a
``json_cutovers`` row land per store (source hash included), so ``_finish_cutovers``
renames each source to ``<name>.migrated-<ts>`` after ``upgrade head`` fully succeeds,
ONLY if the bytes still hash the same. Sources are never deleted.

Revision ID: 0018
Revises: 0017
Create Date: 2026-08-09
"""

import hashlib
import json
from datetime import datetime, timezone

import sqlalchemy as sa
from alembic import op

revision = "202608092050_misc_cutover"
down_revision = "202608092049_misc_and_checkpoints_schema"
branch_labels = None
depends_on = None


def _absorb_one(conn, store: str, src, import_fn, export_fn):
    """Verbatim port of 0012's/0014's/0016's own helper (see any of those revisions for
    the full mechanics the sparser comments below rely on)."""
    if not src.exists():
        return None
    raw = src.read_bytes()  # ONE read: the hash below and the parse see identical bytes
    original = json.loads(raw.decode("utf-8"))
    if not isinstance(original, list):
        raise RuntimeError(
            f"misc cutover: {src} is not a flat JSON list (list expected at the root) "
            f"— got {type(original).__name__}; aborting rather than silently dropping data"
        )
    import_fn(conn, original)
    if export_fn(conn) != original:
        raise RuntimeError(
            f"misc cutover: {store} round-trip verification FAILED — aborting the whole "
            "migration (all four misc sources are one all-or-nothing unit); the "
            "fail-closed wrapper will restore the pre-migrate backup"
        )
    return raw, original


def _stamp_cutover(conn, store: str, src, raw: bytes, now: str) -> None:
    conn.execute(
        sa.text(
            "INSERT INTO json_cutovers (store, source_path, source_hash, migrated_at, renamed) "
            "VALUES (:s, :p, :h, :t, 0)"
        ),
        {"s": store, "p": str(src), "h": hashlib.sha256(raw).hexdigest(), "t": now},
    )


def upgrade() -> None:
    from backend.db import misc_io as mio
    from backend.db.alembic import versions_evidence_source as evsrc
    from backend.db.tables import artifacts, email_snapshots, pending_pushes

    # `uploads` (renamed `attachments` at revision 0041, cross-channel-attachments RFC
    # Phase 2) is no longer a live symbol in backend.db.tables — frozen locally instead of
    # imported, matching revision 0040's own precedent for a table later renamed: at THIS
    # point in migration history (chained from 0017) the table is genuinely still called
    # "uploads", regardless of what backend.db.tables calls it on HEAD today.
    uploads = sa.table(
        "uploads",
        sa.column("seq", sa.Integer),
        sa.column("id", sa.Text),
        sa.column("payload", sa.JSON),
    )

    conn = op.get_bind()
    now = datetime.now(timezone.utc).isoformat()

    def _generic(store: str, table, id_field: str = "id"):
        return (
            lambda c, raw: mio.import_payload_list(c, table, raw, id_field=id_field),
            lambda c: mio.export_payload_list(c, table),
        )

    # (store, source path, import fn, export fn) — order is COSMETIC (no FK between any
    # of the four misc tables).
    jobs = [
        ("artifacts", evsrc.ARTIFACTS_SOURCE, *_generic("artifacts", artifacts)),
        ("uploads", evsrc.UPLOADS_SOURCE, *_generic("uploads", uploads)),
        (
            "email_snapshots",
            evsrc.EMAIL_SNAPSHOTS_SOURCE,
            *_generic("email_snapshots", email_snapshots, id_field="message_id"),
        ),
        (
            "pending_pushes",
            evsrc.PENDING_PUSHES_SOURCE,
            *_generic("pending_pushes", pending_pushes),
        ),
    ]

    for store, src, import_fn, export_fn in jobs:
        result = _absorb_one(conn, store, src, import_fn, export_fn)
        if result is not None:
            raw, _original = result
            _stamp_cutover(conn, store, src, raw, now)


def downgrade() -> None:
    """No-op: revision 0017 owns the DDL for the 4 tables this revision absorbs into
    (plus ``checkpoints``, absorbed separately in a later revision) and drops them when a
    caller downgrades past it — there is nothing for this data-only revision to undo
    beyond its own upgrade()'s row inserts, which vanish along with the tables. The
    renamed ``<name>.migrated-<ts>`` sources (once ``ensure_migrated()`` has run for
    real, not just a bare ``command.upgrade``) are the manual restore path, exactly as
    every other cutover revision in this family documents — an automatic,
    data-destructive downgrade here would be a lie."""
