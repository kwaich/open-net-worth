"""Create `price_movement_notes` — the Notable Price Movement per-session cache.

Mirrors backend.db.payload_store.payload_table("price_movement_notes") exactly (seq/id/payload +
a unique seq index, NO book_id): a derived per-instrument-per-session cache of deterministic
session facts plus the LLM's cited explanation, rebuildable from the price provider's daily
bars and the `evidence` table. Checkpoint-EXCLUDED like `asset_brief` (ai.checkpoint._policy).
No financial fields, no user-authored data.
"""

import sqlalchemy as sa
from alembic import op

revision = "202609201200_price_movement_notes"
down_revision = "202609192200_preserve_automation_creation_origin"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "price_movement_notes",
        sa.Column("seq", sa.Integer, nullable=False),
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("payload", sa.JSON, nullable=False),
    )
    op.create_index("ix_price_movement_notes_seq", "price_movement_notes", ["seq"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_price_movement_notes_seq", table_name="price_movement_notes")
    op.drop_table("price_movement_notes")
