"""instruments: create the instruments payload table + absorb instruments.json with
round-trip verification

Revision ID: 0005
Revises: 0004
Create Date: 2026-08-07
"""

import hashlib
import json
from datetime import datetime, timezone

import sqlalchemy as sa
from alembic import op

revision = "202608071033_instruments_cutover"
down_revision = "202608071032_price_history_cutover"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Mirrors backend.db.payload_store.payload_table("instruments") exactly — seq/id/
    # payload + a unique seq index — so `alembic check` sees no drift against tables.py.
    op.create_table(
        "instruments",
        sa.Column("seq", sa.Integer, nullable=False),
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("payload", sa.JSON, nullable=False),
    )
    op.create_index("ix_instruments_seq", "instruments", ["seq"], unique=True)

    from backend.db.alembic import versions_evidence_source as evsrc
    from backend.db.instruments_io import export_instruments, import_instruments

    src = evsrc.INSTRUMENTS_SOURCE
    if not src.exists():
        return  # fresh install: nothing to absorb
    raw = src.read_bytes()  # ONE read: the hash and the import see identical bytes
    original = json.loads(raw.decode("utf-8"))
    if not isinstance(original, list):
        # InstrumentStore's pre-cutover JsonStore used root_key=None (a flat list) — any
        # other top-level shape means the file was hand-edited or is some other store's
        # data. Abort rather than silently coercing or dropping it (spec D6 step 4).
        raise RuntimeError(
            f"instruments cutover: {src} is not a flat JSON list (root_key=None expected) "
            f"— got {type(original).__name__}; aborting rather than silently dropping data"
        )
    conn = op.get_bind()
    import_instruments(conn, original)
    if export_instruments(conn) != original:  # spec D6 step 4: EXACT round-trip or abort ALL
        raise RuntimeError(
            "instruments cutover round-trip verification FAILED — aborting the migration; "
            "the fail-closed wrapper will restore the pre-migrate backup"
        )
    conn.execute(
        sa.text(
            "INSERT INTO json_cutovers (store, source_path, source_hash, migrated_at, renamed) "
            "VALUES (:s, :p, :h, :t, 0)"
        ),
        {
            "s": "instruments",
            "p": str(src),
            "h": hashlib.sha256(
                raw
            ).hexdigest(),  # _finish_cutovers refuses the rename if the file drifts
            "t": datetime.now(timezone.utc).isoformat(),
        },
    )


def downgrade() -> None:
    op.drop_table("instruments")
    # The source .json.migrated-<ts> file still exists (never deleted) — manual
    # restore is: rename it back. A data-destructive auto-down here would be a lie.
