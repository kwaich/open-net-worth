"""Statements: a name the user gives one (``statements_meta.title``, nullable).

Documents renames a statement (owner, 2026-09-27: rename and delete work for every file, not
only uploads). The name is PROMOTED beside ``filename`` so the Documents list reads it from the
catalog columns, never ``payload`` (which can carry a scanned statement's page images); like
every statement key it is also kept in ``payload``, so export/import carry it. No backfill: a
statement without one keeps its automatic name (account — month).

A plain ``ADD COLUMN``, never ``batch_alter_table``: ``statement_parse_progress`` references
this table ON DELETE CASCADE, and a batch rebuild would drop and recreate it. Downgrade drops
the column (native ``DROP COLUMN``, SQLite 3.35+); the ``payload`` copy stays and is inert.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "202609271900_statement_title"
down_revision = "202609261500_owned_asset_purchase_point_derived"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("statements_meta", sa.Column("title", sa.Text, nullable=True))


def downgrade() -> None:
    op.drop_column("statements_meta", "title")
