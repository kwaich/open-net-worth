"""Reassign ``session_messages.id`` in chronological order where a timestamp exists.

Revision 0053/0054 rebuilt the table copying rows ``ORDER BY session_id, seq`` — alphabetical
by session, not by time. Within a conversation that is correct, and per-session order is all
the application reads. But ``MAX(id)`` then means "the alphabetically-last session", so
"the latest 30 messages" across the database returns whatever session sorts last. An
autoincrement key that does not ascend with time is a trap for the next person who assumes
it does.

Rows are re-copied ordered by the SESSION's earliest timestamp, then by their existing
order within that session. Sorting each message by its OWN ``created_at`` does not work: 92%
of rows have none (they predate ``_stamp_message_times``), so a conversation with mixed
stamped and unstamped messages gets shuffled — the unstamped ones collapse onto one value and
jump ahead of later stamped ones. The first attempt did exactly that and the verification
below refused the swap, which is what it is for.

Session granularity is what "the latest messages" actually needs, and it leaves every
conversation internally byte-identical — verified before the swap, refusing rather than
guessing.

RAW copy on both sides: passing driver TEXT back through a typed ``sa.JSON`` column
double-encodes it (an earlier revision did that to 16,378 live messages).

Revision ID: 0057
Revises: 0056
Create Date: 2026-08-24
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "202608250013_session_message_chronological_ids"
down_revision = "202608241942_drop_proactive_sidecar"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()

    # (session_id -> ordered rows) so per-conversation order can be re-verified after.
    before: dict[str, list] = {}
    for sid, role, content in conn.exec_driver_sql(
        "SELECT session_id, role, content FROM session_messages ORDER BY session_id, id"
    ):
        before.setdefault(str(sid), []).append((role, content))

    op.create_table(
        "session_messages_new",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "session_id",
            sa.Text,
            sa.ForeignKey("sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("role", sa.Text, nullable=False),
        sa.Column("content", sa.JSON, nullable=False),
        sa.Column("created_at", sa.Text, nullable=True),
        sa.Column("updated_at", sa.Text, nullable=True),
        sa.Column("extra", sa.JSON, nullable=True),
    )
    # A session with no stamps anywhere sorts by its own id, keeping it intact and grouped.
    conn.exec_driver_sql(
        "INSERT INTO session_messages_new "
        "(session_id, role, content, created_at, updated_at, extra) "
        "SELECT m.session_id, m.role, m.content, m.created_at, m.updated_at, m.extra "
        "FROM session_messages m "
        "LEFT JOIN (SELECT session_id, MIN(created_at) AS first_at "
        "           FROM session_messages GROUP BY session_id) f "
        "  ON f.session_id = m.session_id "
        # Ordered by the SESSION's own start, then the row's existing order INSIDE that
        # session. Sorting each message by its own `created_at` would shuffle a conversation
        # that has mixed stamped/unstamped rows: the unstamped ones all collapse onto one
        # value and jump ahead of later stamped ones. (The verification below caught exactly
        # that and refused the swap.) Session granularity is what "latest messages" actually
        # needs, and it leaves every conversation internally untouched.
        "ORDER BY COALESCE(f.first_at, ''), m.session_id, m.id"
    )

    after: dict[str, list] = {}
    for sid, role, content in conn.exec_driver_sql(
        "SELECT session_id, role, content FROM session_messages_new ORDER BY session_id, id"
    ):
        after.setdefault(str(sid), []).append((role, content))
    if before != after:
        raise RuntimeError(
            "reordering changed a conversation's own message order — refusing to swap"
        )

    op.drop_index("ix_session_messages_session", table_name="session_messages")
    op.drop_table("session_messages")
    op.rename_table("session_messages_new", "session_messages")
    if conn.dialect.name == "postgresql":
        # Postgres keeps a renamed table's constraint names (SQLite has none to carry); the
        # next rebuild's `_new` names would collide with them.
        op.execute(
            "ALTER TABLE session_messages RENAME CONSTRAINT pk_session_messages_new "
            "TO pk_session_messages"
        )
        op.execute(
            "ALTER TABLE session_messages RENAME CONSTRAINT "
            "fk_session_messages_new_session_id_sessions TO fk_session_messages_session_id_sessions"
        )
    op.create_index("ix_session_messages_session", "session_messages", ["session_id", "id"])


def downgrade() -> None:
    """No-op: ids are opaque ordering keys, and per-conversation order is unchanged."""
