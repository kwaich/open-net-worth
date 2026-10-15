"""session_messages: a transcript becomes a TABLE OF MESSAGES, not one JSON column.

## Why

``sessions.transcript`` held an entire conversation in a single JSON cell. On the owner's
instance at migration time: **843 sessions, 16,810 messages, 71.9 MB**, largest single cell
**3.1 MB** — and rewritten IN FULL by ``SessionStore.set_transcript`` at the end of every
model round.

Two consequences drove everything else:

* **There is no "append a message".** A turn reads the whole array into memory, mutates a
  Python list, then rewrites the column. So an alert arriving from a scheduler thread
  mid-turn could not append — the running turn would clobber the column with its own
  in-memory copy. That is the ONLY reason proactive alerts live in a sidecar and are folded
  in at the next turn's start, and the string of defects in that area (injection-time
  timestamps, a 600-char record cap, an arrival-order count cap that discarded the newest
  alerts) are all downstream of the workaround rather than of alerts themselves.
* **Every read was all-or-nothing.** ``GET /api/sessions/{id}/messages?tail=30`` rehydrated
  megabytes and sliced afterwards.

## Why it is mechanical

The design was ALREADY positional, so ``seq`` is a near-1:1 replacement for the list index:
the failed-turn rollback (``del history[history_snapshot_len:]``), the checkpoint anchor
(``anchor_history_len`` + ``/rewind``'s ``_truncate_session_history``), the fold
``window_start`` and the cost chip's ``turn_index`` were all integers into the same list.
``seq`` is strictly better: a list index shifts if anything is inserted mid-list, a seq does
not.

## Shape

``content`` stays the OPAQUE provider value — a plain string, or the block list carrying
text / tool_use / tool_result / attachment refs — byte-for-byte what the JSON array held.
Splitting blocks into their own table is a separate question and fixes nothing here.

DDL written directly rather than imported from ``backend.db.tables`` — the rule every prior
``create_table`` revision in this history follows (revision 0048's docstring names it): a
migration must stay correct after a later change to the live ORM table.

## Backfill

Forward-only, per-row fault-tolerant, and VERIFIED before the old column is dropped. Every
session's array is read, inserted as rows, and read back; a session whose row count does not
match is reported and the migration REFUSES rather than dropping the column over it — this is
a real user conversation history and a silent partial migration is the one outcome worth
failing loudly to avoid. A non-list/corrupt ``transcript`` is carried across as zero messages
and named in the report, matching ``db/session_io``'s existing tolerance for one bad row.

``sessions.transcript`` is dropped in the SAME revision (owner's explicit choice of the
big-bang cutover, RFC §6 option A) — no dual-read path is left behind to rot.

Revision ID: 0051
Revises: 0050
Create Date: 2026-08-24
"""

from __future__ import annotations

import json
import logging

import sqlalchemy as sa
from alembic import op

revision = "202608241937_session_messages"
down_revision = "202608230747_sql_query_log"
branch_labels = None
depends_on = None

log = logging.getLogger("alembic.session_messages")

# Insert in batches — one 16,810-row executemany is fine on SQLite but needlessly large on
# Postgres, and a batch boundary gives the progress log something honest to say.
_BATCH = 500


def _rows_for(session_id: str, transcript) -> list[dict]:
    """One session's messages as insertable rows. A non-list or unparseable transcript
    yields none — reported by the caller, never silently equated with an empty chat."""
    if isinstance(transcript, str):
        try:
            transcript = json.loads(transcript)
        except Exception:  # noqa: BLE001
            return []
    if not isinstance(transcript, list):
        return []
    rows = []
    for i, m in enumerate(transcript):
        if not isinstance(m, dict):
            continue
        ts = m.get("ts")
        rows.append(
            {
                "session_id": session_id,
                "seq": i,
                "role": str(m.get("role") or ""),
                "content": m.get("content"),
                "ts": int(ts) if isinstance(ts, (int, float)) else None,
            }
        )
    return rows


def upgrade() -> None:
    op.create_table(
        "session_messages",
        sa.Column("session_id", sa.Text, primary_key=True),
        sa.Column("seq", sa.Integer, primary_key=True),
        sa.Column("role", sa.Text, nullable=False),
        sa.Column("content", sa.JSON, nullable=False),
        sa.Column("ts", sa.Integer, nullable=True),
    )
    op.create_index("ix_session_messages_session", "session_messages", ["session_id", "seq"])

    conn = op.get_bind()
    msgs = sa.table(
        "session_messages",
        sa.column("session_id", sa.Text),
        sa.column("seq", sa.Integer),
        sa.column("role", sa.Text),
        sa.column("content", sa.JSON),
        sa.column("ts", sa.Integer),
    )

    sessions_seen = 0
    messages_written = 0
    unreadable: list[str] = []

    rows = conn.execute(sa.text("SELECT id, transcript FROM sessions")).fetchall()
    for sid, transcript in rows:
        sessions_seen += 1
        batch = _rows_for(str(sid), transcript)
        expected = 0
        if isinstance(transcript, list):
            expected = sum(1 for m in transcript if isinstance(m, dict))
        elif isinstance(transcript, str):
            try:
                parsed = json.loads(transcript)
                expected = (
                    sum(1 for m in parsed if isinstance(m, dict))
                    if isinstance(parsed, list)
                    else -1
                )
            except Exception:  # noqa: BLE001
                expected = -1
        elif transcript is not None:
            expected = -1
        if expected < 0:
            unreadable.append(str(sid))
            continue
        for i in range(0, len(batch), _BATCH):
            conn.execute(sa.insert(msgs), batch[i : i + _BATCH])
        messages_written += len(batch)

    # VERIFY before dropping the source. A silent partial migration of the owner's whole
    # conversation history is the one outcome worth refusing over.
    check = conn.execute(sa.text("SELECT COUNT(*) FROM session_messages")).scalar() or 0
    if int(check) != messages_written:
        raise RuntimeError(
            f"session_messages backfill verification failed: inserted {messages_written}, "
            f"table holds {check} — refusing to drop sessions.transcript"
        )
    log.info(
        "session_messages: %d sessions, %d messages migrated%s",
        sessions_seen,
        messages_written,
        f" ({len(unreadable)} unreadable transcript(s): {unreadable[:5]})" if unreadable else "",
    )

    with op.batch_alter_table("sessions") as batch_op:
        batch_op.drop_column("transcript")


def downgrade() -> None:
    """Rebuild ``sessions.transcript`` from the rows, then drop the table.

    Genuinely reversible — the messages carry everything the array held, in order."""
    op.add_column("sessions", sa.Column("transcript", sa.JSON, nullable=True))
    conn = op.get_bind()
    rows = conn.execute(
        sa.text(
            "SELECT session_id, seq, role, content, ts FROM session_messages ORDER BY session_id, seq"
        )
    ).fetchall()
    by_session: dict[str, list] = {}
    for sid, _seq, role, content, ts in rows:
        m = {"role": role, "content": json.loads(content) if isinstance(content, str) else content}
        if ts is not None:
            m["ts"] = ts
        by_session.setdefault(str(sid), []).append(m)
    for sid, msgs in by_session.items():
        conn.execute(
            sa.text("UPDATE sessions SET transcript = :t WHERE id = :i"),
            {"t": json.dumps(msgs), "i": sid},
        )
    conn.execute(sa.text("UPDATE sessions SET transcript = '[]' WHERE transcript IS NULL"))
    op.drop_index("ix_session_messages_session", table_name="session_messages")
    op.drop_table("session_messages")
