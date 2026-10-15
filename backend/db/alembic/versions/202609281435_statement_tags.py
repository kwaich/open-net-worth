"""Statements: the tags the user gives one (``statements_meta.tags``, nullable JSON list).

Documents lets the user tag any item (owner, 2026-09-28: "maybe tags?" — instead of folders).
A statement's tags are PROMOTED beside ``title`` and ``note`` for the same reason: the
Documents list reads the catalog columns, never ``payload`` (which can carry a scanned
statement's page images). Like every statement key they are also kept in ``payload``, so
export/import carry them. No backfill — no statement has any yet.

A plain ``ADD COLUMN``, never ``batch_alter_table``: ``statement_parse_progress`` references
this table ON DELETE CASCADE, and a batch rebuild would drop and recreate it. Downgrade drops
the column (native ``DROP COLUMN``, SQLite 3.35+); the ``payload`` copy stays and is inert.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "202609281435_statement_tags"
down_revision = "202609281300_data_hygiene_cleanup"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("statements_meta", sa.Column("tags", sa.JSON, nullable=True))


def downgrade() -> None:
    op.drop_column("statements_meta", "tags")
