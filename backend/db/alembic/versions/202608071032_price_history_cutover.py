"""price_history: create price_points + absorb price_history.json with round-trip verification

Revision ID: 0004
Revises: 0003
Create Date: 2026-08-07
"""

import hashlib
import json
from datetime import datetime, timezone

import sqlalchemy as sa
from alembic import op

revision = "202608071032_price_history_cutover"
down_revision = "202608070847_write_log"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "price_points",
        sa.Column("price_source_id", sa.Text, primary_key=True),
        sa.Column("date", sa.Text, primary_key=True),
        sa.Column("price", sa.Float, nullable=False),
        sa.Column("source", sa.Text, nullable=False, server_default=""),
    )
    op.create_index("ix_price_points_date", "price_points", ["date"])

    from backend.db.alembic import versions_evidence_source as evsrc
    from backend.db.price_history_io import export_price_history, import_price_history

    src = evsrc.PRICE_HISTORY_SOURCE
    if not src.exists():
        return  # fresh install: nothing to absorb
    raw = src.read_bytes()  # ONE read: the hash and the import see identical bytes
    original = json.loads(raw.decode("utf-8"))
    conn = op.get_bind()
    import_price_history(conn, original)
    if export_price_history(conn) != original:  # spec D6 step 4: EXACT round-trip or abort ALL
        raise RuntimeError(
            "price_history cutover round-trip verification FAILED — aborting the migration; "
            "the fail-closed wrapper will restore the pre-migrate backup"
        )
    conn.execute(
        sa.text(
            "INSERT INTO json_cutovers (store, source_path, source_hash, migrated_at, renamed) "
            "VALUES (:s, :p, :h, :t, 0)"
        ),
        {
            "s": "price_history",
            "p": str(src),
            "h": hashlib.sha256(
                raw
            ).hexdigest(),  # _finish_cutovers refuses the rename if the file drifts
            "t": datetime.now(timezone.utc).isoformat(),
        },
    )


def downgrade() -> None:
    op.drop_table("price_points")
    # The source .json.migrated-<ts> file still exists (never deleted) — manual
    # restore is: rename it back. A data-destructive auto-down here would be a lie.
