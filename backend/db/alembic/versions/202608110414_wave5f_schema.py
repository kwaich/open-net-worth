"""Wave 5F schema: sessions.extraction_cursor. Schema only — data absorption is
revision 0035.

Adds a fifth nullable JSON sidecar column to the `sessions` table (revision 0032, Wave
5E) — `ai/memory.py`'s per-session incremental-extraction watermark
(`{"last_extracted_len": N}`), explicitly flagged out of scope by Wave 5E's own cutover
(revision 0033's docstring) because a cursor file's session_id is not guaranteed to have
a matching session row. See `backend/db/tables.py`'s own `sessions` comment for the full
reasoning.

Unlike every prior wave's own schema revision, this one does not `create_table` — it
`add_column`s onto a table an earlier revision already owns.

Revision ID: 0034
Revises: 0033
Create Date: 2026-08-10
"""

import sqlalchemy as sa
from alembic import op

revision = "202608110414_wave5f_schema"
down_revision = "202608110413_wave5e_cutover"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("sessions", sa.Column("extraction_cursor", sa.JSON, nullable=True))


def downgrade() -> None:
    op.drop_column("sessions", "extraction_cursor")
