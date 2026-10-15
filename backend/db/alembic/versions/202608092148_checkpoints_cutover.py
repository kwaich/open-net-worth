"""checkpoints cutover: absorb the turn-checkpoint manifest into its revision-0017 table
(plan 4e, task 2). Mirrors 0018 (misc cutover)/0016 (configs cutover)/0014 (logs cutover)
exactly: read the source's bytes ONCE, import, EXACT round-trip verify (export == original),
abort the WHOLE migration on any diff — but INLINE, not through a shared io module: unlike
0018's four misc stores, ``checkpoints`` has exactly ONE consumer (this revision; the
CheckpointIndexStore-backed query/prune/orphan surface lives in ``ai.checkpoint._index``, not
an alembic-facing io module), so a ``checkpoint_index_io.py`` would exist for no second
caller — the read/insert/export helpers below are this revision's own copy, matching every
prior cutover revision's convention of being self-contained rather than importing another
revision's helper (0018's own docstring: "Verbatim port of 0002's/0004's/0005's own helper").

SCOPE: absorbs ONLY the checkpoint index (``CHECKPOINTS_INDEX`` — ``DATA_DIR/checkpoints/
index.json``), the flat list of checkpoint manifest entries. ``CHECKPOINTS_BLOBS`` (the
gzip blob pool for Tier-4 file snapshots) is UNRELATED and untouched — it stays exactly the
file-backed pool it always was; only the manifest that references its blob hashes moves.

The source's real basename is ``index.json``, NOT ``checkpoints.json`` — note this at
absorb time rather than assuming the wave's usual ``<table_name>.json`` shape (every other
Wave-E source's basename happens to equal its table name; this one doesn't, since the file
lived under ``CHECKPOINTS_DIR/`` as ``index.json`` rather than ``DATA_DIR/checkpoints.json``).

The list is already in PLAIN APPEND order on disk (`CheckpointContext.finalise()` is the
sole writer, via `_append_index` — an insert, never a prepend), so this uses the plain
ascending-seq shape, no `reverse` flag. The round-trip check is still ORDER-SENSITIVE
regardless (plain ``==`` on a parsed JSON list is positional, not a set/count comparison).

Only once import + round-trip verification has succeeded does a ``json_cutovers`` row land
(source hash included), so ``_finish_cutovers`` (``backend/db/migrate.py`` — a generic
mechanism driven entirely by that table, no per-store code needed here) renames the source
to ``index.json.migrated-<ts>`` after ``upgrade head`` fully succeeds, ONLY if the bytes
still hash the same. The source is never deleted.

Revision ID: 0019
Revises: 0018
Create Date: 2026-08-09
"""

import hashlib
import json
from datetime import datetime, timezone

import sqlalchemy as sa
from alembic import op

revision = "202608092148_checkpoints_cutover"
down_revision = "202608092050_misc_cutover"
branch_labels = None
depends_on = None

_BATCH = 1000


def _import_checkpoints(conn, table, raw: list[dict]) -> int:
    """Absorb a flat JSON list into ``table``, one row per element with an ascending
    ``seq`` (0-based, file order) — the source is already append-order on disk (module
    docstring). Fires the checkpoint write-log hook unconditionally, like every other
    absorb job in this program (misc_io.import_payload_list, planning_io, config_io) —
    a structural no-op here (no turn is active during a boot-time migration, and
    ``checkpoints`` is itself checkpoint-EXCLUDED), never a special-cased absorb path."""
    from backend.core.io import fire_db_write_hook

    rows = [{"seq": seq, "id": row.get("id"), "payload": row} for seq, row in enumerate(raw)]
    if rows:
        fire_db_write_hook(table.name, [(r["id"], None) for r in rows], conn=conn)
    for i in range(0, len(rows), _BATCH):
        conn.execute(sa.insert(table), rows[i : i + _BATCH])
    return len(rows)


def _export_checkpoints(conn, table) -> list[dict]:
    return [r.payload for r in conn.execute(sa.select(table).order_by(table.c.seq))]


def _stamp_cutover(conn, store: str, src, raw: bytes, now: str) -> None:
    conn.execute(
        sa.text(
            "INSERT INTO json_cutovers (store, source_path, source_hash, migrated_at, renamed) "
            "VALUES (:s, :p, :h, :t, 0)"
        ),
        {"s": store, "p": str(src), "h": hashlib.sha256(raw).hexdigest(), "t": now},
    )


def upgrade() -> None:
    from backend.db.alembic import versions_evidence_source as evsrc
    from backend.db.tables import checkpoints

    conn = op.get_bind()
    src = evsrc.CHECKPOINTS_INDEX_SOURCE
    if not src.exists():
        return

    raw = src.read_bytes()  # ONE read: the hash below and the parse see identical bytes
    original = json.loads(raw.decode("utf-8"))
    if not isinstance(original, list):
        raise RuntimeError(
            f"checkpoints cutover: {src} is not a flat JSON list (list expected at the "
            f"root) — got {type(original).__name__}; aborting rather than silently "
            "dropping data"
        )
    _import_checkpoints(conn, checkpoints, original)
    if _export_checkpoints(conn, checkpoints) != original:
        raise RuntimeError(
            "checkpoints cutover: round-trip verification FAILED — aborting; the "
            "fail-closed wrapper will restore the pre-migrate backup"
        )
    now = datetime.now(timezone.utc).isoformat()
    _stamp_cutover(conn, "checkpoints", src, raw, now)


def downgrade() -> None:
    """No-op: revision 0017 owns the DDL for ``checkpoints`` and drops it when a caller
    downgrades past it — there is nothing for this data-only revision to undo beyond its
    own upgrade()'s row inserts, which vanish along with the table. The renamed
    ``index.json.migrated-<ts>`` source (once ``ensure_migrated()`` has run for real, not
    just a bare ``command.upgrade``) is the manual restore path, exactly as every other
    cutover revision in this family documents — an automatic, data-destructive downgrade
    here would be a lie."""
