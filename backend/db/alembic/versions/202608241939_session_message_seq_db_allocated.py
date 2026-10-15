"""session_messages: the house ``(seq, id, payload-ish)`` shape, replacing 0051's list index.

Revision 0051 made ``seq`` the CALLER's list index. Right for the cutover — every consumer
(``del history[n:]``, ``anchor_history_len``, ``window_start``, ``turn_index``) was already
positional, which is what let 16,378 messages move with zero mismatches — and wrong for
CONCURRENT appends, which are the point of the migration: a proactive alert arriving while a
turn runs computes the same "next index" the turn is about to use, from a value that went
stale the instant it was read.

The fix is the shape this codebase already uses everywhere else, not a new one. 29 of 30
primary keys in the schema are ``Text``; ``PayloadTableStore``'s ``(seq, id, payload)`` is the
template, and its ``seq`` is assigned ``max(seq)+1`` INSIDE the write transaction — closing
exactly this race, with a regression test that already names it
(``evidence_store``'s ``test_merge_hits_concurrent_writers_no_seq_collision``).

So: ``id`` is an opaque ``msg_<hex>`` primary key like every other table, and ``seq`` orders
the conversation, allocated under the same lock the write takes. ``seq`` is an ORDERING KEY,
not a position — POSITION is the row's index in the ordered read, which is what the positional
consumers keep using.

An earlier draft of this revision used ``sa.BigInteger`` autoincrement instead. Two things
were wrong with it, and both are worth recording because they are easy to repeat:

* it matched nothing else in the schema — a novel idiom introduced for a problem the
  codebase had already solved;
* on SQLite it does not even work. SQLAlchemy renders ``BigInteger`` as ``BIGINT NOT NULL``,
  and SQLite only auto-allocates a primary key declared *exactly* ``INTEGER PRIMARY KEY``
  (the rowid alias). ``BIGINT PRIMARY KEY`` is an ordinary NOT NULL column, so the copy step
  — which omits ``id`` on purpose so the database assigns it — failed with
  ``NOT NULL constraint failed`` on the first row. It only fired on a table with rows, so an
  empty database "passed" (caught by a teammate against the 140-message demo book;
  ``sa.BigInteger().with_variant(sa.Integer, "sqlite")`` is the fix if that shape is ever
  wanted again).

SHAPE-TOLERANT REBUILD. Because that draft reached some databases and not others, this
revision reads whatever ``session_messages`` currently looks like — 0051/0052's
``(session_id, seq)`` composite PK, or the draft's ``id``-carrying table — and rebuilds it
into the final shape either way, so every instance converges on re-run. Rows are copied in
their existing order, so no conversation is reordered.

Revision ID: 0053
Revises: 0052
Create Date: 2026-08-24
"""

from __future__ import annotations

import uuid

import sqlalchemy as sa
from alembic import op

revision = "202608241939_session_message_seq_db_allocated"
down_revision = "202608241938_session_message_extra"
branch_labels = None
depends_on = None


def _columns(conn, table: str) -> set[str]:
    return {c["name"] for c in sa.inspect(conn).get_columns(table)}


def upgrade() -> None:
    conn = op.get_bind()
    cols = _columns(conn, "session_messages")
    if "id" in cols and "seq" not in cols:
        order_by = "session_id, id"  # the superseded draft's shape
    else:
        order_by = "session_id, seq"

    op.create_table(
        "session_messages_new",
        sa.Column("seq", sa.Integer, nullable=False),
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column(
            "session_id",
            sa.Text,
            # ON DELETE CASCADE, so a message can never outlive its conversation. Before
            # this the cascade was hand-written in `SessionStore.delete` and
            # `session_io.wipe_all_sessions` — correct today, and one new delete path away
            # from leaving orphans keyed to an id nothing references.
            sa.ForeignKey("sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("role", sa.Text, nullable=False),
        sa.Column("content", sa.JSON, nullable=False),
        sa.Column("ts", sa.Integer, nullable=True),
        sa.Column("extra", sa.JSON, nullable=True),
    )

    rows = conn.exec_driver_sql(
        f"SELECT session_id, role, content, ts, extra FROM session_messages ORDER BY {order_by}"
    ).fetchall()
    per_session: dict[str, int] = {}
    payload = []
    for session_id, role, content, ts, extra in rows:
        sid = str(session_id)
        seq = per_session.get(sid, 0)
        per_session[sid] = seq + 1
        payload.append((seq, f"msg_{uuid.uuid4().hex[:12]}", sid, role, content, ts, extra))

    # RAW insert, deliberately. `content`/`extra` are read above as the driver's own TEXT
    # (`exec_driver_sql` bypasses the typed JSON column), so passing them back through a
    # `sa.JSON` column would JSON-ENCODE an already-encoded string — every message would
    # become `"[{\"type\": ...}]"`, a JSON string wrapping JSON, and every NULL `extra`
    # would become the literal string `'null'`. That is exactly what an earlier run of this
    # revision did to 16,378 live messages. Text in, text out: the bytes are unchanged.
    if payload:
        for i in range(0, len(payload), 500):
            conn.exec_driver_sql(
                "INSERT INTO session_messages_new "
                "(seq, id, session_id, role, content, ts, extra) VALUES (?, ?, ?, ?, ?, ?, ?)",
                payload[i : i + 500],
            )

    before = conn.execute(sa.text("SELECT COUNT(*) FROM session_messages")).scalar() or 0
    after = conn.execute(sa.text("SELECT COUNT(*) FROM session_messages_new")).scalar() or 0
    if int(before) != int(after):
        raise RuntimeError(
            f"session_messages rebuild lost rows: {before} -> {after}; refusing to swap"
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
    op.create_index(
        "ix_session_messages_session", "session_messages", ["session_id", "seq"], unique=True
    )


def downgrade() -> None:
    """Back to 0052's ``(session_id, seq)`` composite PK. The stored values are unchanged —
    only the key shape and the surrogate id go away."""
    op.create_table(
        "session_messages_old",
        sa.Column("session_id", sa.Text, primary_key=True),
        sa.Column("seq", sa.Integer, primary_key=True),
        sa.Column("role", sa.Text, nullable=False),
        sa.Column("content", sa.JSON, nullable=False),
        sa.Column("ts", sa.Integer, nullable=True),
        sa.Column("extra", sa.JSON, nullable=True),
    )
    op.execute(
        "INSERT INTO session_messages_old (session_id, seq, role, content, ts, extra) "
        "SELECT session_id, seq, role, content, ts, extra FROM session_messages"
    )
    op.drop_index("ix_session_messages_session", table_name="session_messages")
    op.drop_table("session_messages")
    op.rename_table("session_messages_old", "session_messages")
    if op.get_bind().dialect.name == "postgresql":
        # See upgrade(): Postgres keeps a renamed table's constraint names.
        op.execute(
            "ALTER TABLE session_messages RENAME CONSTRAINT pk_session_messages_old "
            "TO pk_session_messages"
        )
    op.create_index("ix_session_messages_session", "session_messages", ["session_id", "seq"])
