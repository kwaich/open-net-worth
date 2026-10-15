"""Wave 5E schema: the `sessions` table (chat transcripts). Schema only — data
absorption is revision 0033.

`sessions` collapses SESSIONS_DIR's four pre-migration file shapes ({id}.json transcript,
{id}.fold.json, {id}.proactive.json, and the shared index.json metadata list) into one row
per session — see backend/db/tables.py's own `sessions` comment for the full
column-by-column reasoning.

Revision ID: 0032
Revises: 0031
Create Date: 2026-08-10
"""

import sqlalchemy as sa
from alembic import op

revision = "202608110412_wave5e_schema"
down_revision = "202608110411_wave5d2b_cutover"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sessions",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("created_at", sa.Text, nullable=False),
        sa.Column("updated_at", sa.Text, nullable=False),
        sa.Column("message_count", sa.Integer, nullable=False),
        sa.Column("meta", sa.JSON, nullable=False),
        sa.Column("transcript", sa.JSON, nullable=False),
        sa.Column("fold", sa.JSON, nullable=True),
        sa.Column("proactive", sa.JSON, nullable=True),
    )


def downgrade() -> None:
    op.drop_table("sessions")
