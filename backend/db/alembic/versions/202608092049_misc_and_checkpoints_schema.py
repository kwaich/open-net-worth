"""misc + checkpoints schema: artifacts, uploads, email_snapshots, pending_pushes,
checkpoints — the 4 misc-store tables (plan 4e, task 1) PLUS the checkpoint-index table,
front-loaded schema-only (its store class + absorb are task 2 — mirrors Wave A task 1's
own precedent of defining a whole wave's DDL before every store class exists).

All 5 are the plain Tier-2 ``payload_table()`` shape (seq/id/payload, NO book_id): none
of artifacts/uploads/email_snapshots/pending_pushes/checkpoints is book/money-scoped
(session-scoped chat artifacts, a user's own attachment catalog, global email
provenance, a global runtime retry queue, turn-scoped checkpoints). artifacts/uploads
are HYBRID (spec D5) — only the metadata catalog lands here; the document/parsed-payload
bytes stay Tier-4 files, untouched by this revision.

No cross-table FK anywhere in this revision — every one of the 5 tables is independent.

Revision ID: 0017
Revises: 0016
Create Date: 2026-08-09
"""

import sqlalchemy as sa
from alembic import op

revision = "202608092049_misc_and_checkpoints_schema"
down_revision = "202608091739_configs_cutover"
branch_labels = None
depends_on = None

# The 5 tables built straight from payload_table("<name>")'s shape — see tables.py.
_TABLES = (
    "artifacts",
    "uploads",
    "email_snapshots",
    "pending_pushes",
    "checkpoints",
)


def upgrade() -> None:
    for name in _TABLES:
        op.create_table(
            name,
            sa.Column("seq", sa.Integer, nullable=False),
            sa.Column("id", sa.Text, primary_key=True),
            sa.Column("payload", sa.JSON, nullable=False),
        )
        op.create_index(f"ix_{name}_seq", name, ["seq"], unique=True)


def downgrade() -> None:
    for name in reversed(_TABLES):
        op.drop_table(name)
