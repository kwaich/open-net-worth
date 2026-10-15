"""evidence: create tables + absorb evidence.json with round-trip verification

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-07
"""

import hashlib
import json
from datetime import datetime, timezone

import sqlalchemy as sa
from alembic import op

revision = "202608070253_evidence_cutover"
down_revision = "202608070207_identity_and_cutover_ledger"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "evidence",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("seq", sa.Integer, nullable=False),
        sa.Column("expires_at", sa.Text, nullable=False, server_default=""),
        sa.Column("domain", sa.Text, nullable=False, server_default=""),
        sa.Column("created_at", sa.Text, nullable=False, server_default=""),
        sa.Column("payload", sa.JSON, nullable=False),
    )
    op.create_index("ix_evidence_expires_at", "evidence", ["expires_at"])
    op.create_index("ix_evidence_seq", "evidence", ["seq"], unique=True)
    op.create_table(
        "evidence_subjects",
        sa.Column(
            "evidence_id",
            sa.Text,
            sa.ForeignKey("evidence.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("subject", sa.Text, primary_key=True),
    )
    op.create_index("ix_evidence_subjects_subject", "evidence_subjects", ["subject"])

    from backend.db.alembic import versions_evidence_source as evsrc
    from backend.db.evidence_io import export_evidence, import_evidence_json

    src = evsrc.SOURCE
    if not src.exists():
        return  # fresh install: nothing to absorb
    raw = src.read_bytes()  # ONE read: the hash and the import see identical bytes
    original = json.loads(raw.decode("utf-8"))
    conn = op.get_bind()
    import_evidence_json(conn, original)
    if export_evidence(conn) != original:  # spec D6 step 4: EXACT round-trip or abort ALL
        raise RuntimeError(
            "evidence cutover round-trip verification FAILED — aborting the migration; "
            "the fail-closed wrapper will restore the pre-migrate backup"
        )
    conn.execute(
        sa.text(
            "INSERT INTO json_cutovers (store, source_path, source_hash, migrated_at, renamed) "
            "VALUES (:s, :p, :h, :t, 0)"
        ),
        {
            "s": "evidence",
            "p": str(src),
            "h": hashlib.sha256(
                raw
            ).hexdigest(),  # _finish_cutovers refuses the rename if the file drifts
            "t": datetime.now(timezone.utc).isoformat(),
        },
    )


def downgrade() -> None:
    op.drop_table("evidence_subjects")
    op.drop_table("evidence")
    # The source .json.migrated-<ts> file still exists (never deleted) — manual
    # restore is: rename it back. A data-destructive auto-down here would be a lie.
