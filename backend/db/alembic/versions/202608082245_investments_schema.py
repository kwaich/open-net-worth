"""investments schema: positions, lots, grants, vest_events, option_contracts,
realized_events, watchlists, asset_brief — the 8 investment stores (plan 4b, task 1).
Schema only — no data absorption (that is revision 0012).

Six tables mirror ``backend.db.payload_store.book_payload_table()`` exactly: a book_id FK
column, ``seq``/``id``/``payload`` — the money core's shape (0006) PLUS the book-scoped
payload primitive's own ``(book_id, seq, id, payload)`` Tier-1 layout, one unique
``ix_<name>_book_seq`` index per table — identical structure to 0009's planning block.
``watchlists`` is a one-row-per-book singleton keyed BY ``book_id`` (mirrors ``plan_state``).
``asset_brief`` is the ONE exception: a derived per-instrument cache, NOT Tier-1 user data
(absent from the spec's Tier-1 enumeration; its own docstring says "derived cache,
checkpoint-EXCLUDED, like price_history") -> the plain Tier-2 ``payload_table()`` shape
(``seq``/``id``/``payload``, NO ``book_id``), mirroring ``instruments`` (0005). NO
cross-table FK between investment tables — every inter-store link (lot.position_id,
realized.consumptions[].lot_id, vest.grant_id, position.instrument_id, ...) is an OPAQUE id
Python-filtered over ``all()``, never a SQL JOIN; the only FK anywhere in this revision is
``book_id -> books.id``, matching the money core and planning.

Revision ID: 0011
Revises: 0010
Create Date: 2026-08-08
"""

import sqlalchemy as sa
from alembic import op

revision = "202608082245_investments_schema"
down_revision = "202608081826_planning_cutover"
branch_labels = None
depends_on = None

# The 6 tables built straight from book_payload_table("<name>")'s shape — see tables.py.
_BOOK_PAYLOAD_TABLES = (
    "positions",
    "lots",
    "grants",
    "vest_events",
    "option_contracts",
    "realized_events",
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
        "watchlists",
        sa.Column("book_id", sa.Text, sa.ForeignKey("books.id"), primary_key=True),
        sa.Column("payload", sa.JSON, nullable=False),
    )

    # Mirrors backend.db.payload_store.payload_table("asset_brief") exactly — seq/id/
    # payload + a unique seq index, NO book_id (Tier-2 cache, like instruments/0005).
    op.create_table(
        "asset_brief",
        sa.Column("seq", sa.Integer, nullable=False),
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("payload", sa.JSON, nullable=False),
    )
    op.create_index("ix_asset_brief_seq", "asset_brief", ["seq"], unique=True)


def downgrade() -> None:
    op.drop_table("asset_brief")
    op.drop_table("watchlists")
    for name in reversed(_BOOK_PAYLOAD_TABLES):
        op.drop_table(name)
