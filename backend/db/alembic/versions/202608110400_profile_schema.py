"""profile schema: the owner's financial-profile singleton table (Wave 5, JSON->DB). Schema only
— data absorption is revision 0021.

``profile`` is a one-row-per-book singleton keyed BY ``book_id`` (plan_state's exact shape,
revision 0009): ``payload`` = the whole profile dict verbatim. The only FK is
``book_id -> books.id``, matching every other Tier-1 singleton.

Revision ID: 0020
Revises: 0019
Create Date: 2026-08-10
"""

import sqlalchemy as sa
from alembic import op

revision = "202608110400_profile_schema"
down_revision = "202608092148_checkpoints_cutover"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "profile",
        sa.Column("book_id", sa.Text, sa.ForeignKey("books.id"), primary_key=True),
        sa.Column("payload", sa.JSON, nullable=False),
    )


def downgrade() -> None:
    op.drop_table("profile")
