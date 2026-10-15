"""session_messages: ``ts`` becomes ``created_at`` + ``updated_at``, matching ``sessions``.

Two problems with ``ts``:

* **It is a frontend wire name that leaked into storage.** The chat UI reads ``ts`` off each
  rendered message, so the column was named after the JSON field rather than after what it
  holds. ``sessions`` — the parent table, right beside it — uses ``created_at`` /
  ``updated_at`` as TEXT ISO-8601. One table storing epoch-ms under a different name is
  gratuitous inconsistency. ``rehydrate_messages`` already maps DB -> frontend, so the wire
  can keep emitting ``ts`` while storage says what it means.
* **It recorded creation only.** Messages genuinely are edited in place —
  ``_abort_stale_pending`` rewrites a dangling ``tool_use``, and the attachment path swaps a
  resolved message back to its ref-shaped durable form — and nothing recorded that it
  happened.

``created_at`` is backfilled from ``ts`` where one exists (1,343 of 16,378 rows on the
owner's instance) and left NULL otherwise. The other 15,035 predate
``_stamp_message_times`` (added 2026-08-21) and have no true source; inventing a time for
them would be worse than admitting it is unknown. Every message written from here on gets
one at INSERT.

``updated_at`` starts equal to ``created_at`` — a row that has never been edited was last
"updated" when it was written.

Revision ID: 0055
Revises: 0054
Create Date: 2026-08-24
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "202608241941_session_message_timestamps"
down_revision = "202608241940_session_message_rowid"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("session_messages", sa.Column("created_at", sa.Text, nullable=True))
    op.add_column("session_messages", sa.Column("updated_at", sa.Text, nullable=True))
    # epoch-ms -> ISO-8601 UTC, the shape `sessions.created_at` uses. Done in SQL so the
    # whole backfill is one statement rather than 16,378 round trips.
    if op.get_bind().dialect.name == "postgresql":
        iso = (
            "to_char(to_timestamp(ts / 1000) AT TIME ZONE 'UTC', 'YYYY-MM-DD\"T\"HH24:MI:SS') || "
            "  '.' || lpad((ts % 1000)::text, 3, '0') || '000+00:00'"
        )
    else:
        iso = (
            "strftime('%Y-%m-%dT%H:%M:%S', ts / 1000, 'unixepoch') || "
            "  '.' || substr('000' || (ts % 1000), -3) || '000+00:00'"
        )
    op.execute(
        f"UPDATE session_messages SET created_at = {iso}, updated_at = {iso} WHERE ts IS NOT NULL"
    )
    with op.batch_alter_table("session_messages") as batch_op:
        batch_op.drop_column("ts")


def downgrade() -> None:
    op.add_column("session_messages", sa.Column("ts", sa.Integer, nullable=True))
    if op.get_bind().dialect.name == "postgresql":
        epoch_ms = "CAST(EXTRACT(EPOCH FROM CAST(created_at AS timestamptz)) * 1000 AS BIGINT)"
    else:
        epoch_ms = "CAST((julianday(created_at) - 2440587.5) * 86400000 AS INTEGER)"
    op.execute(f"UPDATE session_messages SET ts = {epoch_ms} WHERE created_at IS NOT NULL")
    with op.batch_alter_table("session_messages") as batch_op:
        batch_op.drop_column("created_at")
        batch_op.drop_column("updated_at")
