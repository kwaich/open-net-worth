"""Sessions: a transcript change token (``sessions.transcript_rev``, nullable).

A returning client asks "did this conversation change since I last read it?" before paying for
a transcript re-read. Nothing stored answered that: ``updated_at`` does not move on a plain
append, and ``session_messages.id`` cannot stand in for a version — on SQLite a delete of the
newest row followed by an insert REUSES its id, so an in-place edit (truncate + re-append) can
leave count and max id exactly as they were. Every message write now stamps a fresh random token
here, in the same transaction, with a plain UPDATE (no read-modify-write, so two concurrent
appenders can only ever leave a token neither client has seen). No backfill: a NULL token reads
as "unknown" and the client simply re-reads, as before.

A plain ``ADD COLUMN``, never ``batch_alter_table``: ``session_messages`` references this table
ON DELETE CASCADE, and a batch rebuild deletes every message. Downgrade drops the column (native
``DROP COLUMN``, SQLite 3.35+).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "202610071700_session_transcript_rev"
down_revision = "202610041300_revalidate_stored_category_colors"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("sessions", sa.Column("transcript_rev", sa.Text, nullable=True))


def downgrade() -> None:
    op.drop_column("sessions", "transcript_rev")
