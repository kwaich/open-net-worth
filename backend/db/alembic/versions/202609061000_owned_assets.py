"""owned_assets, owned_asset_categories — the Assets tab domain (ADR 2026-09-06-assets-tab-
faithful-port). Schema only — a brand new domain, no data absorption.

Both tables mirror ``backend.db.payload_store.book_payload_table()`` exactly (the 0009
planning-schema shape): a ``book_id`` FK column, ``seq``/``id``/``payload``, one unique
``ix_<name>_book_seq`` index. No cross-table FK beyond ``book_id -> books.id`` — an asset's
``loan_account_id`` / ``category`` are opaque references resolved by the service, matching
every other Tier-1 store's loose-coupling convention.

Revision ID: 202609061000_owned_assets
Revises: 202609051200_derive_bond_quote_per_unit
Create Date: 2026-09-06
"""

import sqlalchemy as sa
from alembic import op

revision = "202609061000_owned_assets"
down_revision = "202609051200_derive_bond_quote_per_unit"
branch_labels = None
depends_on = None

_TABLES = ("owned_assets", "owned_asset_categories")


def upgrade() -> None:
    for name in _TABLES:
        op.create_table(
            name,
            sa.Column("book_id", sa.Text, sa.ForeignKey("books.id"), nullable=False),
            sa.Column("seq", sa.Integer, nullable=False),
            sa.Column("id", sa.Text, primary_key=True),
            sa.Column("payload", sa.JSON, nullable=False),
        )
        op.create_index(f"ix_{name}_book_seq", name, ["book_id", "seq"], unique=True)


def downgrade() -> None:
    for name in reversed(_TABLES):
        op.drop_table(name)
