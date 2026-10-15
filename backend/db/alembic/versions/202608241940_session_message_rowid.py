"""session_messages: let the DATABASE assign the key. Drops ``seq`` and the surrogate id.

Revisions 0051 and 0053 both had the application decide a message's position, and both were
wrong in the same way.

* **0051** used the caller's list index. Two writers on one session computed the same "next"
  index from a value that went stale the instant they read it.
* **0053** used ``max(seq)+1`` for the session, taken inside ``SessionStore``'s own
  transaction. That is the ``PayloadTableStore`` pattern — but its lock is
  ``engine._lock_for_table``, a **``threading.Lock``**: in-process only. Proven with two
  processes appending to one session::

      OK 18981
      FAILED IntegrityError: UNIQUE constraint failed: session_messages.session_id, seq

  The unique index turned corruption into a loud failure, which is the right backstop, but
  the write still failed. ``uvicorn --workers``, a CLI script, or the scheduler in a separate
  process all hit it. This codebase has already learned this once: CLAUDE.md records the
  proactive queue's own ``_PROACTIVE_LOCK`` (a process-local ``RLock``) being retired
  because the same guarantee had to come from the database instead.

So the key is the database's job. ``id INTEGER PRIMARY KEY`` is SQLite's rowid alias:
assigned inside the INSERT, monotonic, unique, with no read beforehand and no lock to depend
on. On Postgres SQLAlchemy renders the same declaration as an identity column. ``seq`` and
the ``msg_<hex>`` surrogate both go away — one column now does the one job.

(``sa.Integer`` specifically. ``sa.BigInteger`` renders ``BIGINT NOT NULL`` on SQLite, which
is NOT the rowid alias, so nothing is auto-assigned and the copy fails with
``NOT NULL constraint failed`` on the first row — an earlier draft of 0053 did exactly that,
and it only shows up on a table that already has rows.)

``id`` orders the conversation; it is not a position. POSITION is the row's index in the
ordered read, which is what the positional consumers use.

Rows are copied ordered by the existing ``(session_id, seq)``, so ids ascend in the current
order and no conversation is reordered. The copy is RAW text in, raw text out — passing
driver TEXT back through a typed ``sa.JSON`` column double-encodes it, which an earlier 0053
run did to 16,378 live messages.

Revision ID: 0054
Revises: 0053
Create Date: 2026-08-24
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "202608241940_session_message_rowid"
down_revision = "202608241939_session_message_seq_db_allocated"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "session_messages_new",
        # INTEGER (not BigInteger) — SQLite's rowid alias, so the database assigns it.
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "session_id",
            sa.Text,
            sa.ForeignKey("sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("role", sa.Text, nullable=False),
        sa.Column("content", sa.JSON, nullable=False),
        sa.Column("ts", sa.Integer, nullable=True),
        sa.Column("extra", sa.JSON, nullable=True),
    )
    conn = op.get_bind()
    rows = conn.exec_driver_sql(
        "SELECT session_id, role, content, ts, extra FROM session_messages ORDER BY session_id, seq"
    ).fetchall()
    for i in range(0, len(rows), 500):
        conn.exec_driver_sql(
            "INSERT INTO session_messages_new (session_id, role, content, ts, extra) "
            "VALUES (?, ?, ?, ?, ?)",
            [tuple(r) for r in rows[i : i + 500]],
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
    op.create_index("ix_session_messages_session", "session_messages", ["session_id", "id"])


def downgrade() -> None:
    op.create_table(
        "session_messages_old",
        sa.Column("seq", sa.Integer, nullable=False),
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column(
            "session_id",
            sa.Text,
            sa.ForeignKey("sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("role", sa.Text, nullable=False),
        sa.Column("content", sa.JSON, nullable=False),
        sa.Column("ts", sa.Integer, nullable=True),
        sa.Column("extra", sa.JSON, nullable=True),
    )
    import uuid

    conn = op.get_bind()
    rows = conn.exec_driver_sql(
        "SELECT session_id, role, content, ts, extra FROM session_messages ORDER BY session_id, id"
    ).fetchall()
    counters: dict[str, int] = {}
    payload = []
    for session_id, role, content, ts, extra in rows:
        sid = str(session_id)
        seq = counters.get(sid, 0)
        counters[sid] = seq + 1
        payload.append((seq, f"msg_{uuid.uuid4().hex[:12]}", sid, role, content, ts, extra))
    for i in range(0, len(payload), 500):
        conn.exec_driver_sql(
            "INSERT INTO session_messages_old (seq, id, session_id, role, content, ts, extra) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            payload[i : i + 500],
        )
    op.drop_index("ix_session_messages_session", table_name="session_messages")
    op.drop_table("session_messages")
    op.rename_table("session_messages_old", "session_messages")
    if conn.dialect.name == "postgresql":
        # See upgrade(): Postgres keeps a renamed table's constraint names.
        op.execute(
            "ALTER TABLE session_messages RENAME CONSTRAINT pk_session_messages_old "
            "TO pk_session_messages"
        )
        op.execute(
            "ALTER TABLE session_messages RENAME CONSTRAINT "
            "fk_session_messages_old_session_id_sessions "
            "TO fk_session_messages_session_id_sessions"
        )
    op.create_index(
        "ix_session_messages_session", "session_messages", ["session_id", "seq"], unique=True
    )
