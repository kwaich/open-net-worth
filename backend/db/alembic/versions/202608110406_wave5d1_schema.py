"""wave5d1 schema: secret_config, mcp_clients — the two new Wave 5D1 (JSON->DB, secrets/
config) tables. Schema only, no data absorption (0027).

``secret_config`` is a NEW namespace-keyed singleton table — the SECRET counterpart to
``settings``/``runtime_state`` (same (namespace, payload) shape; see ``db/tables.py``'s own
docstring for why the 15 connector-credential configs + connector_sessions can't share
``runtime_state``: SECRET_TABLES/NON_RESTORABLE_TABLES need to name a table that holds ONLY
secrets, not one that also holds runtime_state's non-secret harness_state watermark).

``mcp_clients`` is the generic (seq, id, payload) shape every flat-list store rides — list-
shaped (each configured external MCP server is its own row), unlike its 15 namespace-singleton
siblings.

Revision ID: 0026
Revises: 0025
Create Date: 2026-08-10
"""

import sqlalchemy as sa
from alembic import op

revision = "202608110406_wave5d1_schema"
down_revision = "202608110405_wave5c_cutover"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "secret_config",
        sa.Column("namespace", sa.Text, primary_key=True),
        sa.Column("payload", sa.JSON, nullable=False),
    )
    op.create_table(
        "mcp_clients",
        sa.Column("seq", sa.Integer, nullable=False),
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("payload", sa.JSON, nullable=False),
    )
    op.create_index("ix_mcp_clients_seq", "mcp_clients", ["seq"], unique=True)


def downgrade() -> None:
    op.drop_table("mcp_clients")
    op.drop_table("secret_config")
