"""Rename the accounts JSON document column to data.

The contents remain byte-for-byte the same account document; this is terminology only.
"""

from alembic import op

revision = "202609121100_rename_account_payload_to_data"
down_revision = "202609121000_normalize_account_institutions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("accounts", "payload", new_column_name="data")


def downgrade() -> None:
    op.alter_column("accounts", "data", new_column_name="payload")
