"""write_log: row-level checkpoint machinery for DB-backed stores (spec D7)

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-07
"""

import sqlalchemy as sa
from alembic import op

revision = "202608070847_write_log"
down_revision = "202608070253_evidence_cutover"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "write_log",
        sa.Column("checkpoint_id", sa.Text, primary_key=True),
        sa.Column("seq", sa.Integer, primary_key=True),
        sa.Column("table_name", sa.Text, nullable=False),
        sa.Column("pk", sa.Text, nullable=False),
        sa.Column("before_payload", sa.JSON, nullable=True),
        sa.Column("created_at", sa.Text, nullable=False, server_default=""),
    )
    op.create_index("ix_write_log_checkpoint", "write_log", ["checkpoint_id"])


def downgrade() -> None:
    op.drop_table("write_log")
