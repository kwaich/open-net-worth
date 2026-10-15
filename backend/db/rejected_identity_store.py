"""RejectedIdentityStore — the external-ref identities of deleted transactions.

One record per (source, ref, movement) of a row the user deleted, written by the ledger's
delete choke and read by both create chokes so the same source event cannot be booked again
once it has been rejected (ADR 2026-09-02-a-deleted-identity-is-remembered). DB-backed
(table ``rejected_identities`` — backend/db/tables.py) via ``BookPayloadTableStore``:

    id               : str   (primary key: sha256("source|ref|from_account_id|to_account_id")[:16])
    source           : str | None
    ref              : str
    from_account_id  : str | None
    to_account_id    : str | None
    txn_id           : str   (the deleted row)
    date             : str | None
    description      : str | None
    amount           : float | None   (display only — the deleted row's primary leg)
    currency         : str | None
    rejected_at      : str   (ISO 8601 UTC)

The key is the MOVEMENT-scoped identity ``matching._batch_l0`` uses, not the bare ref: one
Wise confirmation legitimately books two legs on two routes, and deleting one of them must
not reject the other.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone

from sqlalchemy import Connection

from backend.db.payload_store import BookPayloadTableStore
from backend.db.tables import rejected_identities


def make_rejection_id(
    source: str | None, ref: str, from_account_id: str | None, to_account_id: str | None
) -> str:
    payload = "|".join([source or "", ref, from_account_id or "", to_account_id or ""])
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


class RejectedIdentityStore(BookPayloadTableStore):
    def __init__(self, engine=None):
        super().__init__(rejected_identities, id_field="id", engine=engine)

    def remember(self, entries: list[dict], *, conn: Connection | None = None) -> list[dict]:
        """Upsert one record per entry; ``id`` and ``rejected_at`` are stamped here."""
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        out: list[dict] = []
        for entry in entries:
            stamped = {
                **entry,
                "id": make_rejection_id(
                    entry.get("source"),
                    entry["ref"],
                    entry.get("from_account_id"),
                    entry.get("to_account_id"),
                ),
                "rejected_at": entry.get("rejected_at") or now,
            }
            out.append(self.upsert(stamped, conn=conn))
        return out

    def index(self, *, conn: Connection | None = None) -> dict[tuple[str | None, str], list[dict]]:
        """Every record, grouped by ``(source, ref)`` — one read per write choke, never per row."""
        grouped: dict[tuple[str | None, str], list[dict]] = {}
        for item in self.all(conn=conn):
            grouped.setdefault((item.get("source"), item.get("ref")), []).append(item)
        return grouped

    def forget(self, rejection_id: str, *, conn: Connection | None = None) -> bool:
        return self.delete(rejection_id, conn=conn)

    def forget_for_txns(self, txn_ids: list[str], *, conn: Connection | None = None) -> int:
        wanted = set(txn_ids)
        removed = 0
        for item in self.all(conn=conn):
            if item.get("txn_id") in wanted and self.delete(item["id"], conn=conn):
                removed += 1
        return removed

    def recent(self, limit: int = 50, offset: int = 0) -> list[dict]:
        items = sorted(self.all(), key=lambda r: r.get("rejected_at") or "", reverse=True)
        return items[offset : offset + limit]

    def total_count(self) -> int:
        return self.count()
