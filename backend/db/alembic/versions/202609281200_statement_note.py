"""Statements: a note the user writes on one (``statements_meta.note``, nullable).

Documents lets the user write a note on any item (owner, 2026-09-28). A statement's note is
PROMOTED beside ``title`` for the same reason: the Documents list reads the catalog columns,
never ``payload`` (which can carry a scanned statement's page images). Like every statement
key it is also kept in ``payload``, so export/import carry it. No backfill — no statement
has one yet.

A plain ``ADD COLUMN``, never ``batch_alter_table``: ``statement_parse_progress`` references
this table ON DELETE CASCADE, and a batch rebuild would drop and recreate it. Downgrade drops
the column (native ``DROP COLUMN``, SQLite 3.35+); the ``payload`` copy stays and is inert.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "202609281200_statement_note"
down_revision = "202609271900_statement_title"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("statements_meta", sa.Column("note", sa.Text, nullable=True))


def downgrade() -> None:
    op.drop_column("statements_meta", "note")
