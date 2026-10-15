"""Device-local folder grants and durable, review-only desktop file intake."""

import sqlalchemy as sa
from alembic import op

revision = "202610011200_desktop_access_and_ingest"
down_revision = "202609301800_notification_channels_per_kind_and_per_alert"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for name in ("desktop_access_grants", "desktop_ingest_jobs"):
        op.create_table(
            name,
            sa.Column("seq", sa.Integer, nullable=False),
            sa.Column("id", sa.Text, primary_key=True),
            sa.Column("payload", sa.JSON, nullable=False),
        )
        op.create_index(f"ix_{name}_seq", name, ["seq"], unique=True)


def downgrade() -> None:
    for name in ("desktop_ingest_jobs", "desktop_access_grants"):
        op.drop_index(f"ix_{name}_seq", table_name=name)
        op.drop_table(name)
