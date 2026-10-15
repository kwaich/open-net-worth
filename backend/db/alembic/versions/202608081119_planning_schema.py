"""planning schema: category_groups, categories, commitments, envelopes, income_events,
recurring_streams, targets, corrections, match_decisions, match_reviews, budget_assignments,
plan_state — the 12 planning stores (plan 4a, task 1). Schema only — no data absorption (that
is revision 0010).

Ten tables mirror ``backend.db.payload_store.book_payload_table()`` exactly: a book_id FK
column, ``seq``/``id``/``payload`` — the money core's shape (0006) PLUS the book-scoped
payload primitive's own ``(book_id, seq, id, payload)`` Tier-1 layout, one unique
``ix_<name>_book_seq`` index per table. ``budget_assignments`` is hand-built (relational
``book_id``/``month``/``category``/``amount``, no ``seq`` — a keyed relation has no insertion
order); ``plan_state`` is a one-row-per-book singleton keyed BY ``book_id``. NO cross-table FK
between planning tables — categories/envelopes/groups are referenced by OPAQUE id only,
loose coupling is deliberate; the only FK anywhere in this revision is ``book_id ->
books.id``, matching the money core.

Revision ID: 0009
Revises: 0008
Create Date: 2026-08-08
"""

import sqlalchemy as sa
from alembic import op

revision = "202608081119_planning_schema"
down_revision = "202608080315_money_decimal"
branch_labels = None
depends_on = None

# The 10 tables built straight from book_payload_table("<name>")'s shape — see tables.py.
_BOOK_PAYLOAD_TABLES = (
    "category_groups",
    "categories",
    "commitments",
    "envelopes",
    "income_events",
    "recurring_streams",
    "targets",
    "corrections",
    "match_decisions",
    "match_reviews",
)


def upgrade() -> None:
    for name in _BOOK_PAYLOAD_TABLES:
        op.create_table(
            name,
            sa.Column("book_id", sa.Text, sa.ForeignKey("books.id"), nullable=False),
            sa.Column("seq", sa.Integer, nullable=False),
            sa.Column("id", sa.Text, primary_key=True),
            sa.Column("payload", sa.JSON, nullable=False),
        )
        op.create_index(f"ix_{name}_book_seq", name, ["book_id", "seq"], unique=True)

    op.create_table(
        "budget_assignments",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("book_id", sa.Text, sa.ForeignKey("books.id"), nullable=False),
        sa.Column("month", sa.Text, nullable=False),
        sa.Column("category", sa.Text, nullable=False),
        sa.Column("amount", sa.Text, nullable=False),
    )
    op.create_index("ix_budget_assignments_book_month", "budget_assignments", ["book_id", "month"])

    op.create_table(
        "plan_state",
        sa.Column("book_id", sa.Text, sa.ForeignKey("books.id"), primary_key=True),
        sa.Column("payload", sa.JSON, nullable=False),
    )


def downgrade() -> None:
    op.drop_table("plan_state")
    op.drop_table("budget_assignments")
    for name in reversed(_BOOK_PAYLOAD_TABLES):
        op.drop_table(name)
