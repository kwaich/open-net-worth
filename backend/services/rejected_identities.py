"""Rejected identities — a deleted transaction's external refs are remembered, not freed.

``TransactionStore.by_external_ref`` reads live rows only, so a delete FREES the ref: the
"unblock after an accidental delete" guarantee. It is also the loop that re-booked a voided
hotel charge the day after the owner deleted it — the email was re-delivered, the ref was free,
the row came back. This module is the memory that closes the loop (ADR
2026-09-02-a-deleted-identity-is-remembered):

- ``remember_deleted`` runs inside the ledger's delete choke, one record per (ref, movement)
  of every row removed. Batch ROLLBACK and trade UNDO callers opt out — rolling back is not
  rejecting.
- ``rejections_for`` is consulted by both create chokes OUTSIDE the ``force`` gate, after L0.
  A live row holding the ref is "already recorded" and wins; only a ref no live row holds
  can be a rejection.
- ``forget``/``forget_matching`` are the way back: a ``create`` decision at the tool, or the
  settings UI. Resurrecting a row forgets its records, so the next delete starts clean.

Identity here is the MOVEMENT-scoped one ``matching._batch_l0`` uses — ``(source, ref)`` plus
the from/to accounts — not the bare ref, because one Wise confirmation legitimately books two
legs on two routes and deleting one must not reject the other.
"""

from __future__ import annotations

import logging

from sqlalchemy import Connection

from backend.core.errors import PreviouslyDeletedError
from backend.core.txn_sources import ext_ref_pairs
from backend.db.rejected_identity_store import RejectedIdentityStore

logger = logging.getLogger(__name__)

store = RejectedIdentityStore()


def _movement(row: dict) -> tuple[str | None, str | None]:
    return row.get("from_account_id"), row.get("to_account_id")


def _primary_leg(row: dict) -> tuple[float | None, str | None]:
    for amt_key, ccy_key in (
        ("amount", "currency"),
        ("from_amount", "from_currency"),
        ("to_amount", "to_currency"),
    ):
        if row.get(amt_key) is not None:
            return row[amt_key], row.get(ccy_key)
    return None, None


def remember_deleted(rows: list[dict], *, conn: Connection | None = None) -> list[dict]:
    """Record every (ref, movement) identity of ``rows``. Merge tombstones write nothing —
    the canonical row inherited their refs and still holds them live."""
    entries: list[dict] = []
    for row in rows:
        if row.get("merged_into"):
            continue
        from_acct, to_acct = _movement(row)
        amount, currency = _primary_leg(row)
        for source, ref in sorted(ext_ref_pairs(row), key=lambda p: (p[0] or "", p[1])):
            entries.append(
                {
                    "source": source,
                    "ref": ref,
                    "from_account_id": from_acct,
                    "to_account_id": to_acct,
                    "txn_id": row.get("id"),
                    "date": row.get("date"),
                    "description": row.get("description"),
                    "amount": amount,
                    "currency": currency,
                }
            )
    if not entries:
        return []
    return store.remember(entries, conn=conn)


def rejections_for(rows: list[dict], *, conn: Connection | None = None) -> dict[int, dict]:
    """Index → the rejection record each row's refs hit on the same movement. One store read
    for the whole batch. Rows must carry resolved account IDS — a name never matches."""
    if not any(row.get("external_refs") for row in rows):
        return {}
    index = store.index(conn=conn)
    if not index:
        return {}
    hits: dict[int, dict] = {}
    for i, row in enumerate(rows):
        movement = _movement(row)
        for pair in sorted(ext_ref_pairs(row), key=lambda p: (p[0] or "", p[1])):
            record = next((r for r in index.get(pair, []) if _movement(r) == movement), None)
            if record is not None:
                hits[i] = record
                break
    return hits


def rejection_for(row: dict, *, conn: Connection | None = None) -> dict | None:
    return rejections_for([row], conn=conn).get(0)


def previously_deleted_error(record: dict) -> PreviouslyDeletedError:
    when = (record.get("rejected_at") or "")[:10]
    facts = [
        str(record.get("date") or ""),
        (
            f"{float(record['amount']):,.2f} {record.get('currency') or ''}".strip()
            if record.get("amount") is not None
            else ""
        ),
        repr(record.get("description")) if record.get("description") else "",
    ]
    described = ", ".join(f for f in facts if f)
    return PreviouslyDeletedError(
        f"Refused: this is a transaction you already deleted ({described}; ref "
        f"{record.get('source')}:{record.get('ref')}, deleted {when}). It was not booked "
        "again. If the user explicitly wants it back, re-submit with a `create` decision for "
        "this row; otherwise leave it deleted.",
        rejection_id=record.get("id"),
        deleted_txn_id=record.get("txn_id"),
        rejected_at=record.get("rejected_at"),
        source=record.get("source"),
        ref=record.get("ref"),
    )


def forget(rejection_id: str) -> bool:
    return store.forget(rejection_id)


def forget_for_txns(txn_ids: list[str], *, conn: Connection | None = None) -> int:
    if not txn_ids:
        return 0
    return store.forget_for_txns(txn_ids, conn=conn)


def forget_matching(rows: list[dict], *, conn: Connection | None = None) -> int:
    """Resurrection: drop every record the given rows would have hit."""
    removed = 0
    for record in rejections_for(rows, conn=conn).values():
        if store.forget(record["id"], conn=conn):
            removed += 1
    return removed


def list_rejected(limit: int = 50, offset: int = 0) -> dict:
    items = store.recent(limit, offset=offset)
    return {"rejected": items, "total": store.total_count(), "limit": limit, "offset": offset}
