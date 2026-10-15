"""EmailSnapshotStore — a provenance snapshot of the email content the
email-transaction-logger agent read at fetch time (EM1, docs/qa/acceptance/email-source-snapshot.md).

Durable user evidence, not a disposable cache — checkpoint-INCLUDED (unlike its three
misc-store siblings this wave), mirroring its JsonStore-era ``fail_closed_on_corrupt=True``
posture: a data restore correctly covers it, the same way any other user-authored store does.

Row shape: ``{message_id, subject, from_addr, received_at, body_text, referenced, stashed_at}``.
Keyed/deduped by ``message_id`` — a re-stash of an already-known message is a stable no-op
(the first-seen snapshot wins; see ``stash``). ``referenced=True`` marks a snapshot that backs
a real booked transaction (promoted by ``services.email_snapshots``) — those rows are exempt
from pruning; every other (never-booked) row is bounded at stash time.

PLAN 4E TASK 1: DB-backed (Tier-2 — table ``email_snapshots``, backend/db/tables.py) via the
generic ``PayloadTableStore`` (seq/id/payload) shape, ``id_field="message_id"`` (mapped onto
the table's generic ``id`` column, mirroring ``AssetBriefStore``'s ``id_field="instrument_id"``
precedent). FULL MIGRATE — no blob directory, every field (including ``body_text``) lives
inline in the row already, so nothing besides the storage backend changes.
"""

from __future__ import annotations

from sqlalchemy import delete as sa_delete
from sqlalchemy import insert as sa_insert
from sqlalchemy import select

from backend.db.payload_store import PayloadTableStore
from backend.db.tables import email_snapshots


class EmailSnapshotStore(PayloadTableStore):
    def __init__(self, engine=None):
        super().__init__(email_snapshots, id_field="message_id", engine=engine)

    def get(self, message_id: str) -> dict | None:
        return self.find_by_id(message_id)

    def stash(self, row: dict) -> dict:
        """Insert a new snapshot keyed by ``message_id``. FIRST-SEEN-WINS: a re-stash of
        an id that's already present is a no-op (the existing row — including its
        ``referenced`` flag — is left untouched); returns the row that ends up stored (new
        or pre-existing). Implemented as check-then-insert inside ONE transaction — NEVER
        a raw ``upsert()`` call, which would silently start overwriting ``referenced=True``
        rows."""
        with self._use_conn(None) as conn:
            existing = self._row(conn, row["message_id"])
            if existing is not None:
                return existing["payload"]
            self._fire_hook(conn, [(row["message_id"], None)])
            seq = self._next_seq(conn)
            conn.execute(
                sa_insert(self._table).values(**self._insert_row(seq, row["message_id"], row))
            )
        return row

    def promote(self, message_id: str) -> bool:
        """Flip ``referenced`` to True. Idempotent — a second promote of the same id is a
        harmless no-op. Returns False when the message_id has no stashed row."""
        updated = self.update_by_id(message_id, {"referenced": True})
        return updated is not None

    def prune_unreferenced(self, keep: int) -> int:
        """Delete UNREFERENCED rows beyond the most recent ``keep`` (by ``stashed_at``,
        newest first). ``referenced=True`` rows are never touched. Bulk delete in ONE
        transaction — the ``CorrectionStore._append_capped`` idiom. Returns the count
        removed."""
        with self._use_conn(None) as conn:
            rows = [dict(r._mapping) for r in conn.execute(select(self._table))]
            unreferenced = sorted(
                (r for r in rows if not r["payload"].get("referenced")),
                key=lambda r: r["payload"].get("stashed_at") or "",
                reverse=True,
            )
            stale = unreferenced[keep:]
            if not stale:
                return 0
            self._fire_hook(conn, [(r["id"], r) for r in stale])
            conn.execute(
                sa_delete(self._table).where(self._table.c.id.in_([r["id"] for r in stale]))
            )
        return len(stale)
