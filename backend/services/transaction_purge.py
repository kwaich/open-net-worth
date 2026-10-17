"""Delete a transaction PERMANENTLY — the row and every copy of its contents the app holds.

An ordinary delete is a soft delete: the row keeps its contents behind ``deleted`` so checkpoint
undo (Lucius's turns) and the re-import guard work. That is the right default and it stays. This
is the explicit other verb, for a transaction the owner wants GONE (ADR
``2026-10-17-delete-permanently-erases-a-transactions-contents``). One unit of work:

* the ordinary delete choke first (``bulk_delete_transactions`` composed on this conn), so its
  refusals hold — a Starting Balance is still refused — and a live row's re-import guard is
  written exactly as a plain delete writes it;
* refused OUTRIGHT, before anything changes, while an investment or commitment fact still
  points at the row (a lot, a vest, a dividend, a commitment's payment): a soft delete leaves
  the row for those to read, a purge would not;
* every merge tombstone folded into it goes too — the same payment from another source;
* the row and its splits/refs are hard-deleted (``TransactionStore.delete``);
* its edit history is cut to stubs (``TransactionAuditStore.redact_for_txns``) plus ONE
  ``delete`` event saying it was deleted permanently;
* its re-import guard keeps only the reference and accounts — the date, description and amount
  it stored for display go;
* every checkpoint image of the row (``write_log``) becomes "did not exist", so no Undo can
  bring it back; review-queue, dedup-decision, correction and merge-journal rows naming it are
  dropped;
* its email snapshot goes when no other transaction holds that message; its payee goes when
  nothing else names it and the owner never configured it.

After commit: receipts linked only to it are deleted (their files too), receipts shared with
another transaction are unlinked — ``receipts`` owns that write and its file-after-commit
order. Never touched: files the owner uploaded (a statement still shows the line), backups
already made, Lucius conversations that mention it. On SQLite the purge runs with
``secure_delete`` on and checkpoints the WAL, so freed pages do not keep the bytes.
"""

from __future__ import annotations

import logging

from sqlalchemy import Connection, Table, cast, delete, select, update
from sqlalchemy import Text as SAText

from backend.core.errors import ConflictError, ValidationError
from backend.db import tables
from backend.db.engine import locked_unit_of_work
from backend.db.transaction_audit_store import TransactionAuditStore

logger = logging.getLogger(__name__)

# A row these still point at is part of an investment or commitment fact.
_BLOCKING_TABLES = (
    "lots",
    "realized_events",
    "income_events",
    "vest_events",
    "grants",
    "owned_assets",
    "option_contracts",
    "positions",
    "commitments",
)
# Rows that exist only ABOUT the purged transaction: dropped with it.
_DEPENDENT_TABLES = ("match_reviews", "match_decisions", "corrections")
# The stores that can name a payee by id (an Automation's payee condition, a recurring stream,
# a commitment, a target, an alert, a correction, a review). A payee one of them names is kept.
# An explicit list, not every table: an account force-delete erases hundreds of payees at once
# under the ledger lock.
_PAYEE_REFERRERS = (
    "automations",
    "recurring_streams",
    "commitments",
    "targets",
    "alerts",
    "envelopes",
    "corrections",
    "match_reviews",
    "match_decisions",
)
_DISPLAY_FIELDS = ("date", "description", "amount", "currency")
_PURGE_NOTE = "Deleted permanently. Its details were erased."


def _table(name: str) -> Table:
    return tables.metadata.tables[name]


def _mentioning(conn: Connection, name: str, token: str, column: str = "payload") -> list:
    """Rows of ``name`` whose JSON ``column`` contains ``token`` as a quoted value. Ids are
    opaque unique tokens (``txn_<hex>``), so the quoted form cannot match a substring."""
    table = _table(name)
    if column not in table.c:
        return []
    pattern = '%"' + token.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + '"%'
    query = select(table).where(cast(table.c[column], SAText).like(pattern, escape="\\"))
    return [dict(r._mapping) for r in conn.execute(query)]


def _blockers(conn: Connection, ids: list[str]) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for txn_id in ids:
        hits = [name for name in _BLOCKING_TABLES if _mentioning(conn, name, txn_id)]
        if hits:
            found[txn_id] = hits
    return found


def _with_tombstones(conn: Connection, ids: list[str]) -> list[str]:
    """``ids`` plus every merge tombstone folded into one of them, transitively."""
    t = _table("transactions")
    out, frontier = list(dict.fromkeys(ids)), list(dict.fromkeys(ids))
    while frontier:
        rows = conn.execute(select(t.c.id).where(t.c.merged_into.in_(frontier))).all()
        frontier = [r.id for r in rows if r.id not in out]
        out += frontier
    return out


def _forget_checkpoint_images(conn: Connection, ids: list[str]) -> int:
    """Every checkpoint image of these rows becomes "did not exist": a restore's replay then
    deletes the pk and never re-inserts it (``ai.checkpoint._restore._replay_write_log``)."""
    wl = _table("write_log")
    id_set, changed = set(ids), 0
    for txn_id in ids:
        for row in _mentioning(conn, "write_log", txn_id, column="before_payload"):
            image = row["before_payload"] or {}
            mine = (row["table_name"] == "transactions" and row["pk"] in id_set) or (
                row["table_name"] in ("transaction_splits", "transaction_external_refs")
                and image.get("txn_id") in id_set
            )
            # A payload-table image is the stored ROW: ``txn_id`` lives inside its ``payload``.
            payload = {**(image.get("payload") or {})}
            if row["table_name"] == "rejected_identities" and payload.get("txn_id") in id_set:
                for key in _DISPLAY_FIELDS:
                    payload.pop(key, None)
                new_image = {**image, "payload": payload}
            elif mine:
                new_image = None
            else:
                continue
            conn.execute(
                update(wl)
                .where(wl.c.checkpoint_id == row["checkpoint_id"], wl.c.seq == row["seq"])
                .values(before_payload=new_image)
            )
            changed += 1
    return changed


def _strip_rejection_details(conn: Connection, ids: list[str]) -> None:
    t = _table("rejected_identities")
    for txn_id in ids:
        for row in _mentioning(conn, "rejected_identities", txn_id):
            payload = row["payload"] or {}
            if payload.get("txn_id") != txn_id:
                continue
            clean = {k: v for k, v in payload.items() if k not in _DISPLAY_FIELDS}
            conn.execute(update(t).where(t.c.id == row["id"]).values(payload=clean))


def _drop_dependents(conn: Connection, ids: list[str]) -> int:
    dropped = 0
    for name in _DEPENDENT_TABLES:
        table = _table(name)
        for txn_id in ids:
            rows = _mentioning(conn, name, txn_id)
            if rows:
                conn.execute(delete(table).where(table.c.id.in_([r["id"] for r in rows])))
                dropped += len(rows)
    return dropped


def _drop_merge_journal(conn: Connection, ids: list[str]) -> int:
    """A purged row can never be un-merged, and the journal holds its before-image to allow
    exactly that — so either side's entry goes."""
    t = _table("merge_journal")
    gone = delete(t).where(t.c.absorbed_id.in_(ids) | t.c.canonical_id.in_(ids))
    return conn.execute(gone).rowcount


def _email_refs(rows: list[dict]) -> set[str]:
    return {
        str(ref.get("ref"))
        for row in rows
        for ref in row.get("external_refs") or []
        if isinstance(ref, dict) and ref.get("source") == "email" and ref.get("ref")
    }


def _drop_email_snapshots(conn: Connection, rows: list[dict]) -> int:
    """A snapshot of an email no remaining transaction (live or deleted) was booked from."""
    refs = _table("transaction_external_refs")
    snapshots = _table("email_snapshots")
    dropped = 0
    for message_id in _email_refs(rows):
        still_held = conn.execute(
            select(refs.c.ref_id).where(refs.c.source == "email", refs.c.ref == message_id)
        ).first()
        if still_held is None:
            dropped += conn.execute(delete(snapshots).where(snapshots.c.id == message_id)).rowcount
    return dropped


def _configured(payee: dict) -> bool:
    """A payee the owner shaped by hand. A learned default category is not that: booking one
    categorised transaction writes it."""
    from backend.services.payees import _normalize_payee

    extra_aliases = set(payee.get("aliases") or []) - {_normalize_payee(payee.get("name") or "")}
    return bool(
        payee.get("rules")
        or extra_aliases
        or payee.get("hidden")
        or payee.get("autocategorize") is False
    )


def _drop_orphan_payees(conn: Connection, payee_ids: set[str]) -> int:
    payees, txns = _table("payees"), _table("transactions")
    dropped = 0
    for payee_id in payee_ids:
        row = conn.execute(select(payees).where(payees.c.id == payee_id)).first()
        if row is None or _configured(row.payload or {}):
            continue
        if conn.execute(select(txns.c.id).where(txns.c.payee_id == payee_id)).first():
            continue
        if any(_mentioning(conn, name, payee_id) for name in _PAYEE_REFERRERS):
            continue
        conn.execute(delete(payees).where(payees.c.id == payee_id))
        dropped += 1
    return dropped


def _secure_delete(conn: Connection) -> None:
    if conn.dialect.name == "sqlite":
        conn.exec_driver_sql("PRAGMA secure_delete = ON")


def _purge_rows(conn: Connection, ids: list[str]) -> dict:
    from backend.services import ledger
    from backend.services.ledger import transactions

    rows = {txn_id: transactions.find_by_id(txn_id, conn=conn) for txn_id in ids}
    missing = [txn_id for txn_id, row in rows.items() if row is None]
    if missing:
        raise ValidationError(f"Transaction not found: {', '.join(missing)}")
    blocked = _blockers(conn, ids)
    if blocked:
        detail = "; ".join(f"{t} is used by {', '.join(names)}" for t, names in blocked.items())
        raise ConflictError(
            "can't delete permanently while other records still use it — undo the trade, "
            f"vest, dividend or payment first, or use an ordinary delete ({detail})"
        )
    live = [txn_id for txn_id, row in rows.items() if not row.get("deleted")]
    if live:
        ledger.bulk_delete_transactions(live, conn=conn)
    every = _with_tombstones(conn, ids)
    doomed = [r for r in (transactions.find_by_id(t, conn=conn) for t in every) if r is not None]
    _secure_delete(conn)
    for row in doomed:
        transactions.delete(row["id"], conn=conn)
    from backend.services import audit as audit_service

    TransactionAuditStore().redact_for_txns(every, conn=conn)
    for txn_id in ids:
        audit_service.record_txn_event(
            "delete",
            txn_id,
            source=rows[txn_id].get("source"),
            before=None,
            after=None,
            note=_PURGE_NOTE,
            conn=conn,
        )
    return {"purged": every, **erase_traces(conn, every, doomed, audit_done=True)}


def erase_traces(
    conn: Connection, txn_ids: list[str], rows: list[dict], *, audit_done: bool = False
) -> dict:
    """Everything after the hard delete, for rows ALREADY removed on ``conn`` — shared with
    the account force-delete (``ledger.delete_account``), which hard-deletes its own cascade.
    The caller must hold ``TRACE_TABLES``' locks (``locked_unit_of_work``)."""
    if not audit_done:
        TransactionAuditStore().redact_for_txns(txn_ids, conn=conn)
    _strip_rejection_details(conn, txn_ids)
    return {
        "checkpoint_images": _forget_checkpoint_images(conn, txn_ids),
        "dependents": _drop_dependents(conn, txn_ids) + _drop_merge_journal(conn, txn_ids),
        "email_snapshots": _drop_email_snapshots(conn, rows),
        "payees": _drop_orphan_payees(conn, {r["payee_id"] for r in rows if r.get("payee_id")}),
    }


# The tables ``erase_traces`` writes, for a caller's ``locked_unit_of_work``.
TRACE_TABLE_NAMES = (
    "txn_audit",
    "rejected_identities",
    "write_log",
    "payees",
    "email_snapshots",
    "merge_journal",
    *_DEPENDENT_TABLES,
)


def trace_tables() -> list[Table]:
    return [_table(name) for name in TRACE_TABLE_NAMES]


def _locked_tables() -> list[Table]:
    return [_table("transactions"), *trace_tables()]


def _clean_receipts(ids: list[str]) -> list[str]:
    """Receipts linked only to a purged transaction are deleted, shared ones unlinked. Returns
    the receipt ids that could not be cleaned (reported to the owner, never hidden)."""
    from backend.services.receipts import apply_receipt_change, link_store, transactions_for_receipt

    left: list[str] = []
    purged = set(ids)
    for txn_id in ids:
        for link in link_store.by_transaction(txn_id):
            receipt_id = link["receipt_id"]
            others = set(transactions_for_receipt(receipt_id)) - purged
            change = (
                {"type": "unlink", "link_id": link["id"]}
                if others
                else {"type": "delete", "receipt_id": receipt_id, "force": True}
            )
            try:
                apply_receipt_change(change)
            except Exception:  # noqa: BLE001 — reported in the result, never silent
                logger.warning("purge: receipt %s not cleaned", receipt_id, exc_info=True)
                left.append(receipt_id)
    return left


def _checkpoint_wal(conn_engine) -> None:
    try:
        with conn_engine.connect() as conn:
            if conn.dialect.name == "sqlite":
                conn.exec_driver_sql("PRAGMA wal_checkpoint(TRUNCATE)")
    except Exception:  # noqa: BLE001 — best effort; the rows are already gone
        logger.warning("purge: WAL checkpoint failed", exc_info=True)


def after_commit(txn_ids: list[str]) -> list[str]:
    """The post-commit half: receipts (their own choke and file order) and the WAL. Returns
    the receipt ids that could not be cleaned."""
    left = _clean_receipts(txn_ids)
    from backend.db.engine import get_engine

    _checkpoint_wal(get_engine())
    return left


def purge_transactions(ids: list[str]) -> dict:
    """Permanently delete ``ids`` (see the module docstring). Returns what was erased."""
    ids = [str(i).strip() for i in ids or [] if str(i).strip()]
    if not ids:
        raise ValidationError("At least one transaction id is required")
    with locked_unit_of_work(*_locked_tables()) as conn:
        result = _purge_rows(conn, list(dict.fromkeys(ids)))
    result["receipts_not_cleaned"] = after_commit(result["purged"])
    logger.info(
        "purge: %d transaction(s) erased (%d checkpoint images, %d payees)",
        len(result["purged"]),
        result["checkpoint_images"],
        result["payees"],
    )
    return result
