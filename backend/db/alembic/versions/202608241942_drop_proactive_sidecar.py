"""Retire the proactive sidecar — alerts are ordinary messages now.

The sidecar existed for exactly one reason: ``sessions.transcript`` was a single JSON column
that a running turn rewrote wholesale, so an alert arriving from a scheduler thread mid-turn
could not be appended — it would be clobbered. It was staged in this column instead and
spliced into the next turn behind a synthetic "[Automated monitor]" trigger, coalesced by
subject and capped by count and characters.

That workaround caused every alert defect this codebase has seen: the caps discarded the
owner's "Mail scan" alert and Lucius then denied it existed; the injection stamped every
alert with the moment the user next replied rather than when it was sent; the synthetic
trigger turn named a conversation.

Revisions 0051-0055 replaced the transcript with ``session_messages``, so an append is now a
plain INSERT with a database-assigned key, and ``record_proactive_message`` takes the same
session lock a turn already holds. The alert lands in its true chronological place, in full.
Nothing stages, so the column has nothing to hold.

Anything still queued when this runs is FLUSHED into the conversation first rather than
dropped — a staged alert was really sent to the user, and deleting it would silently erase
something they were told.

Revision ID: 0056
Revises: 0055
Create Date: 2026-08-24
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

import sqlalchemy as sa
from alembic import op

log = logging.getLogger("alembic.drop_proactive")

revision = "202608241942_drop_proactive_sidecar"
down_revision = "202608241941_session_message_timestamps"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()

    # SELF-HEAL. An earlier version of this revision DID drop `sessions.proactive`, which on
    # SQLite recreates the table and — via `session_messages.session_id ... ON DELETE
    # CASCADE` — deletes every message with it. Any database that ran that version is left
    # without the column while the ORM still declares it, so every read of `sessions` fails
    # with `no such column: sessions.proactive` (a live HTTP 500 on the demo instance).
    # ADD COLUMN does not recreate the table, so putting it back is safe and cheap.
    cols = {c["name"] for c in sa.inspect(conn).get_columns("sessions")}
    if "proactive" not in cols and conn.dialect.name == "sqlite":
        conn.exec_driver_sql("ALTER TABLE sessions ADD COLUMN proactive JSON")
        log.warning("restored sessions.proactive — an earlier 0056 had dropped it")
    flushed = 0
    rows = conn.exec_driver_sql(
        "SELECT id, proactive FROM sessions WHERE proactive IS NOT NULL"
    ).fetchall()
    for session_id, raw in rows:
        try:
            staged = json.loads(raw) if isinstance(raw, str) else raw
        except Exception:  # noqa: BLE001 — one unreadable queue must not stop the drop
            continue
        if not isinstance(staged, list):
            continue
        for ev in staged:
            if isinstance(ev, dict):
                text, when = ev.get("summary") or "", ev.get("created_at")
            else:
                text, when = str(ev or ""), None
            if not text.strip():
                continue
            when = when or datetime.now(timezone.utc).isoformat()
            conn.exec_driver_sql(
                "INSERT INTO session_messages (session_id, role, content, created_at, updated_at) "
                "VALUES (?, 'assistant', ?, ?, ?)",
                (str(session_id), json.dumps(text), when, when),
            )
            flushed += 1
    # THE COLUMN IS DELIBERATELY LEFT IN PLACE. Dropping it is not worth what it costs:
    # SQLite cannot drop a column in place, so `batch_alter_table` renames `sessions`,
    # recreates it, copies, and DROPS the old table — and `session_messages.session_id`
    # references `sessions.id` ON DELETE CASCADE, so that drop deletes EVERY MESSAGE.
    # Measured on the owner's own database: 16,378 messages -> 12.
    #
    # `PRAGMA foreign_keys=OFF` does NOT save it: SQLite silently ignores the pragma inside
    # a transaction, and alembic runs every migration in one. It looked like a guard and did
    # nothing.
    #
    # An unused nullable column costs a few bytes per row and nothing else. The ORM table no
    # longer declares it, nothing reads or writes it, and a database that never had it is
    # equally fine. That is a far better trade than a rebuild of the parent table with a
    # cascade pointed at the user's entire chat history.
    if flushed:
        log.info("flushed %d staged proactive alert(s) into the conversation", flushed)


def downgrade() -> None:
    """Nothing to undo: the column was never dropped, and the flushed alerts are real
    messages the user was really sent."""
