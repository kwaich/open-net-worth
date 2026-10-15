"""receipts + transaction_receipts: durable transaction evidence, independent of
AttachmentStore and StatementStore.

``.codex/rfcs/cross-channel-attachments-vision-and-statement-evidence.md`` section 2.2 and
``docs/adr/2026-08-15-separate-attachments-receipts-and-statements.md`` ("Transaction
receipts") — Phase 4 items 23/24. Schema-only: both tables are brand new, no prior JSON
file to cut over (unlike revision 0041's ``uploads`` -> ``attachments`` rename), so this
revision creates them empty, mirroring revision 0039's own "schema, not cutover" shape.

``receipts`` is the HYBRID metadata catalog (bytes stay files under RECEIPTS_DIR, exactly
like ``attachments``/RECEIPTS_DIR's sibling ATTACHMENTS_DIR). ``transaction_receipts`` is
the many-to-many link table (a transaction may cite zero or more receipts; one receipt may
support more than one transaction) — a genuine link table, not a foreign key on either
side. Both are bare ``(seq, id, payload)`` shapes — see ``backend/db/tables.py``'s own
comment for why neither promotes extra columns.

Revision ID: 0042
Revises: 0041
Create Date: 2026-08-16
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "202608161811_receipt_store"
down_revision = "202608161716_attachment_store"
branch_labels = None
depends_on = None

_TABLES = ("receipts", "transaction_receipts")


def upgrade() -> None:
    for table in _TABLES:
        op.create_table(
            table,
            sa.Column("seq", sa.Integer, nullable=False),
            sa.Column("id", sa.Text, primary_key=True),
            sa.Column("payload", sa.JSON, nullable=False),
        )
        op.create_index(f"ix_{table}_seq", table, ["seq"], unique=True)


def downgrade() -> None:
    for table in _TABLES:
        op.drop_table(table)
