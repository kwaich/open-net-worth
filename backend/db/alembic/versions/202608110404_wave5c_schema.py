"""wave5c schema: runtime_state, skills_state, subscriptions, actions — 4 more Wave 5C
(JSON->DB) tables. Schema only, no data absorption (0025).

``dedup_config`` needs NO new table here — it lands as a new namespace row in the
EXISTING ``settings`` table (revision 0015), sharing its checkpoint-INCLUDED,
non-portable semantics exactly.

``runtime_state`` is a NEW namespace-keyed singleton table — the checkpoint-EXCLUDED
counterpart to ``settings`` (same (namespace, payload) shape; see ``db/tables.py``'s own
docstring for why ``harness_state`` can't share ``settings``).

``skills_state``/``subscriptions``/``actions`` are the generic (seq, id, payload) shape
every flat-list/keyed-dict store rides — no book_id, no FK beyond none (none of the five
Wave 5C stores is book-scoped user-ledger data).

Revision ID: 0024
Revises: 0023
Create Date: 2026-08-10
"""

import sqlalchemy as sa
from alembic import op

revision = "202608110404_wave5c_schema"
down_revision = "202608110403_wave5b_logs_cutover"
branch_labels = None
depends_on = None

_PAYLOAD_TABLES = ("skills_state", "subscriptions", "actions")


def upgrade() -> None:
    op.create_table(
        "runtime_state",
        sa.Column("namespace", sa.Text, primary_key=True),
        sa.Column("payload", sa.JSON, nullable=False),
    )
    for name in _PAYLOAD_TABLES:
        op.create_table(
            name,
            sa.Column("seq", sa.Integer, nullable=False),
            sa.Column("id", sa.Text, primary_key=True),
            sa.Column("payload", sa.JSON, nullable=False),
        )
        op.create_index(f"ix_{name}_seq", name, ["seq"], unique=True)


def downgrade() -> None:
    for name in reversed(_PAYLOAD_TABLES):
        op.drop_table(name)
    op.drop_table("runtime_state")
