"""Receipts — the write choke for transaction evidence (cross-channel-attachments RFC
section 2.2/2.3; ADR ``2026-08-15-separate-attachments-receipts-and-statements.md``
"Transaction receipts").

``apply_receipt_change(change)`` is the ONE entrypoint for every receipt mutation —
promote-from-attachment, attach-an-existing-receipt, unlink, delete — mirroring
``services.equity.apply_equity_change``'s change-dict shape (CLAUDE.md's write-choke-point
table). A receipt is created only through explicit action here: never by a generic chat
upload (``services.attachment_ingress``, item 21) and never implicitly by the statement
importer. Promoting the SAME chat attachment twice is a no-op that returns the existing
receipt (idempotent by ``source_attachment_id`` — RFC item 25's fail-closed rule already
requires the CALLER to have decided promotion is warranted; this choke's own job is only
"don't duplicate the row", not re-judging that decision). Linking the same (transaction,
receipt) pair twice is a no-op that returns the existing link.

Every attach/unlink/create/delete records ``core.audit_context.get_audit_scope()`` onto the
link row's ``audit_context`` (attach/unlink) — the same ContextVar seam the ledger choke
reads, so a receipt link traces to who made it exactly like a transaction edit does. A
receipt DELETE while transaction links still exist is REFUSED unless the caller passes
``force=True`` — "must never silently remove transaction evidence" (the ADR's own words) —
and the refusal names every transaction that would lose evidence so the caller can decide
with the full picture, never guessing.

``receipts``/``transaction_receipts`` are checkpoint-INCLUDED (reversed 2026-08-21 — see
the amendment on ADR ``2026-08-15-separate-attachments-receipts-and-statements.md``): a
``/rewind`` of a turn that attached, unlinked, or deleted a receipt now reverts it exactly
like any other Lucius write, via the ordinary row-level ``write_log`` mechanism
(``ai.checkpoint._context.CheckpointContext.record_db_before``) — no special handling
needed here, since neither table is in ``ai.checkpoint._policy._EXCLUDED_TABLES`` anymore.

This choke still does NOT call ``core.write_receipt.record_write`` (the SEPARATE
write-then-verify CHIP mechanism, unrelated to checkpointing despite the name) — not
because of checkpoint status, but because ``"receipt"`` is not yet a registered
``services.mentions`` type (``tests/test_write_receipt_multi_type.py::RECORDED_ENTITY_TYPES``
is a deliberately closed, hand-curated set — an unregistered type is dropped by the reader
rather than rendering a blank chip). Wiring receipts into that chip is a separate future
change (a chip renderer + a mention descriptor), not a consequence of this one.
``tests/test_receipts_service.py`` pins the current no-call behavior.
"""

from __future__ import annotations

import base64
import dataclasses
import hashlib

from backend.core.audit_context import get_audit_scope
from backend.core.errors import ConflictError, NotFoundError, ValidationError
from backend.db.engine import locked_unit_of_work
from backend.db.receipt_store import ReceiptStore
from backend.db.tables import receipts as receipts_table
from backend.db.tables import transaction_receipts as transaction_receipts_table
from backend.db.transaction_receipt_store import TransactionReceiptStore

receipt_store = ReceiptStore()
link_store = TransactionReceiptStore()


def _audit_context_snapshot() -> dict:
    scope = get_audit_scope()
    return dataclasses.asdict(scope) if scope is not None else {}


def _require_transaction(transaction_id: str) -> dict:
    from backend.services import ledger as ledger_service

    txn = ledger_service.get_transaction(transaction_id)
    if txn is None:
        raise NotFoundError(f"Transaction {transaction_id!r} not found (or deleted).")
    return txn


def _link(transaction_id: str, receipt_id: str, *, conn) -> dict:
    """Idempotent link — the existing row if this exact pair is already linked."""
    existing = link_store.find_link(transaction_id, receipt_id)
    if existing is not None:
        return existing
    return link_store.link(
        transaction_id, receipt_id, audit_context=_audit_context_snapshot(), conn=conn
    )


def apply_receipt_change(change: dict) -> dict:
    """The single write entrypoint for all receipt mutations.

    ``change["type"]`` ∈ {upload, promote_from_attachment, attach_existing, unlink, rename,
    note, tags, delete}.
    """
    ctype = change.get("type")
    if ctype == "upload":
        return _upload(change)
    if ctype == "promote_from_attachment":
        return _promote_from_attachment(change)
    if ctype == "attach_existing":
        return _attach_existing(change)
    if ctype == "unlink":
        return _unlink(change)
    if ctype == "rename":
        return _rename(change)
    if ctype == "note":
        return _note(change)
    if ctype == "tags":
        return _tags(change)
    if ctype == "delete":
        return _delete(change)
    return {"error": f"unknown change type: {ctype}"}


def _rename(change: dict) -> dict:
    """A name of the user's own for the receipt (Documents). ``None``/empty goes back to the
    automatic one — its transaction's payee."""
    receipt_id = (change.get("receipt_id") or "").strip()
    if not receipt_id:
        raise ValidationError("receipt_id is required.")
    title = " ".join(str(change.get("title") or "").split())[:160] or None
    with locked_unit_of_work(receipts_table) as conn:
        if receipt_store.find_by_id(receipt_id, conn=conn) is None:
            raise NotFoundError(f"Receipt {receipt_id!r} not found.")
        receipt_store.update_by_id(receipt_id, {"title": title}, conn=conn)
        return {"receipt": receipt_store.find_by_id(receipt_id, conn=conn)}


def _note(change: dict) -> dict:
    """The user's own note on the receipt (Documents). ``None``/empty removes it."""
    from backend.services.vault import MAX_NOTE

    receipt_id = (change.get("receipt_id") or "").strip()
    if not receipt_id:
        raise ValidationError("receipt_id is required.")
    note = str(change.get("note") or "").strip() or None
    if note and len(note) > MAX_NOTE:
        raise ValidationError(f"A note can be up to {MAX_NOTE} characters.")
    with locked_unit_of_work(receipts_table) as conn:
        if receipt_store.find_by_id(receipt_id, conn=conn) is None:
            raise NotFoundError(f"Receipt {receipt_id!r} not found.")
        receipt_store.update_by_id(receipt_id, {"note": note}, conn=conn)
        return {"receipt": receipt_store.find_by_id(receipt_id, conn=conn)}


def _tags(change: dict) -> dict:
    """The user's tags on the receipt (Documents). An empty list removes them all."""
    from backend.services.vault import clean_tags

    receipt_id = (change.get("receipt_id") or "").strip()
    if not receipt_id:
        raise ValidationError("receipt_id is required.")
    tags = clean_tags(change.get("tags"))
    with locked_unit_of_work(receipts_table) as conn:
        if receipt_store.find_by_id(receipt_id, conn=conn) is None:
            raise NotFoundError(f"Receipt {receipt_id!r} not found.")
        receipt_store.update_by_id(receipt_id, {"tags": tags}, conn=conn)
        return {"receipt": receipt_store.find_by_id(receipt_id, conn=conn)}


def _upload(change: dict) -> dict:
    """Save a freshly-picked file directly as a receipt — the bytes given ARE the receipt,
    verbatim, never routed through ``attachment_parse.parse_upload``. That generic parser
    exists to make a file usable by Lucius in chat (extract text, decode an image for
    display) — a receipt doesn't need any of that, it just needs the original bytes back
    later, so this is the direct "save it as-is" path for the drawer's own upload button.
    (``promote_from_attachment`` stays the right choke for the genuinely different case of
    linking a file that's already an attachment elsewhere in the conversation.)"""
    content = change.get("content")
    if not isinstance(content, (bytes, bytearray)):
        raise ValidationError("content (raw bytes) is required.")
    content = bytes(content)
    if not content:
        raise ValidationError("Uploaded file is empty.")
    filename = (change.get("filename") or "").strip()
    media_type = change.get("media_type")
    transaction_id = (change.get("transaction_id") or "").strip() or None
    if transaction_id:
        _require_transaction(transaction_id)

    with locked_unit_of_work(receipts_table, transaction_receipts_table) as conn:
        receipt_id = receipt_store.create(
            content,
            filename=filename,
            media_type=media_type,
            content_hash=hashlib.sha256(content).hexdigest(),
            conn=conn,
        )
        receipt = receipt_store.find_by_id(receipt_id, conn=conn)
        link = _link(transaction_id, receipt["id"], conn=conn) if transaction_id else None
    return {"receipt": receipt, "link": link}


def _receipt_bytes_from_entry(
    entry: dict, meta_media_type: str | None
) -> tuple[bytes | None, str | None]:
    """Raw bytes + media_type to store for a receipt, from an ``attachment_parse.parse_upload``
    entry. Every branch except ``kind="image"`` keeps raw ``content`` bytes on the entry
    (see attachment_parse.py — including a scanned, text-less PDF's ``kind="pdf_pages"``,
    which ALSO keeps the original bytes alongside its per-page rendered PNGs, so promoting it
    stores the real file, not a lossy first-page substitute). ``kind="image"`` alone stashes
    base64 ``data`` instead of ``content`` — the shape a generic chat upload's own
    inline-image rendering needs — so that's the one fallback this function still needs."""
    content = entry.get("content")
    if isinstance(content, (bytes, bytearray)):
        return bytes(content), meta_media_type

    data_b64 = entry.get("data")
    if isinstance(data_b64, str) and data_b64:
        return base64.b64decode(data_b64), entry.get("media_type") or meta_media_type

    return None, None


def _promote_from_attachment(change: dict) -> dict:
    """Copy an attachment's bytes into ReceiptStore (or reuse the receipt already promoted
    from this SAME attachment id) and, when ``transaction_id`` is given, link it."""
    from backend.services.attachments import get_attachment, get_attachment_meta, private_refusal

    attachment_id = (change.get("attachment_id") or "").strip()
    if not attachment_id:
        raise ValidationError("attachment_id is required.")
    # A receipt is readable by read_receipts — copying a Private file into one would hand
    # its contents to the model by the back door.
    if private_refusal(attachment_id):
        raise ValidationError(
            "That file is marked Private in Documents, so it can't become a receipt — "
            "switch Private off first."
        )
    transaction_id = (change.get("transaction_id") or "").strip() or None
    if transaction_id:
        _require_transaction(transaction_id)

    # The idempotency check MUST run inside the same lock/transaction as the insert it
    # guards — checking beforehand and never re-verifying is a classic TOCTOU: two
    # near-simultaneous promotions of the same attachment_id could both observe "no
    # existing receipt" and both insert, producing two rows for one attachment. The
    # in-process table lock `locked_unit_of_work` takes (backend.db.engine._lock_for_table)
    # serializes concurrent callers, so a re-check taken AFTER acquiring it is guaranteed
    # to see any row a just-finished concurrent call already committed.
    with locked_unit_of_work(receipts_table, transaction_receipts_table) as conn:
        existing = _find_receipt_for_attachment(attachment_id)
        if existing is not None:
            receipt = existing
        else:
            meta = get_attachment_meta(attachment_id)
            if meta is None:
                raise NotFoundError(f"Attachment {attachment_id!r} not found.")
            entry = get_attachment(attachment_id) or {}
            content, media_type = _receipt_bytes_from_entry(entry, meta.get("media_type"))
            if content is None:
                raise ValidationError(
                    f"Attachment {attachment_id!r} has no raw bytes to promote as a receipt."
                )
            receipt_id = receipt_store.create(
                content,
                filename=meta.get("filename") or "",
                media_type=media_type,
                content_hash=meta.get("content_hash") or hashlib.sha256(content).hexdigest(),
                source_attachment_id=attachment_id,
                conn=conn,
            )
            receipt = receipt_store.find_by_id(receipt_id, conn=conn)
        link = _link(transaction_id, receipt["id"], conn=conn) if transaction_id else None
    return {"receipt": receipt, "link": link}


def _find_receipt_for_attachment(attachment_id: str) -> dict | None:
    for row in receipt_store.all():
        if row.get("source_attachment_id") == attachment_id:
            return row
    return None


def _attach_existing(change: dict) -> dict:
    receipt_id = (change.get("receipt_id") or "").strip()
    transaction_id = (change.get("transaction_id") or "").strip()
    if not receipt_id or not transaction_id:
        raise ValidationError("receipt_id and transaction_id are both required.")
    if receipt_store.find_by_id(receipt_id) is None:
        raise NotFoundError(f"Receipt {receipt_id!r} not found.")
    _require_transaction(transaction_id)
    with locked_unit_of_work(transaction_receipts_table) as conn:
        link = _link(transaction_id, receipt_id, conn=conn)
    return {"link": link}


def _unlink(change: dict) -> dict:
    link_id = (change.get("link_id") or "").strip()
    if not link_id:
        transaction_id = (change.get("transaction_id") or "").strip()
        receipt_id = (change.get("receipt_id") or "").strip()
        if not transaction_id or not receipt_id:
            raise ValidationError("Provide link_id, or both transaction_id and receipt_id.")
        found = link_store.find_link(transaction_id, receipt_id)
        if found is None:
            raise NotFoundError(
                f"No link between transaction {transaction_id!r} and receipt {receipt_id!r}."
            )
        link_id = found["id"]
    with locked_unit_of_work(transaction_receipts_table) as conn:
        row = link_store.find_by_id(link_id, conn=conn)
        if row is None:
            raise NotFoundError(f"Receipt link {link_id!r} not found.")
        link_store.unlink(link_id, conn=conn)
    return {
        "unlinked": link_id,
        "transaction_id": row["transaction_id"],
        "receipt_id": row["receipt_id"],
    }


def _delete(change: dict) -> dict:
    receipt_id = (change.get("receipt_id") or "").strip()
    if not receipt_id:
        raise ValidationError("receipt_id is required.")
    force = bool(change.get("force"))
    row = receipt_store.find_by_id(receipt_id)
    if row is None:
        raise NotFoundError(f"Receipt {receipt_id!r} not found.")
    links = link_store.by_receipt(receipt_id)
    if links and not force:
        txn_ids = [link_row["transaction_id"] for link_row in links]
        raise ConflictError(
            f"Receipt {receipt_id!r} is still linked to {len(links)} transaction(s) "
            f"({', '.join(txn_ids)}) — deleting it would silently remove that evidence. "
            "Pass force=true to delete anyway (this also removes the links)."
        )
    # Re-verify inside the same lock/transaction as the delete: a concurrent
    # attach_existing/promote_from_attachment could have added a NEW link between the
    # snapshot read above and the lock being acquired here. That link would not be in
    # `links`, would survive the unlink loop below, and would be left pointing at a
    # deleted receipt — a dangling transaction_receipts row citing evidence that no
    # longer exists. Re-fetching under the lock and refusing (rather than silently
    # deleting against the stale snapshot) is the same fix `_unlink` already applies via
    # its own `find_by_id(conn=conn)` re-check.
    with locked_unit_of_work(receipts_table, transaction_receipts_table) as conn:
        if receipt_store.find_by_id(receipt_id, conn=conn) is None:
            raise NotFoundError(f"Receipt {receipt_id!r} not found.")
        current_links = link_store.by_receipt(receipt_id)
        if current_links and not force:
            txn_ids = [link_row["transaction_id"] for link_row in current_links]
            raise ConflictError(
                f"Receipt {receipt_id!r} is still linked to {len(current_links)} "
                f"transaction(s) ({', '.join(txn_ids)}) — deleting it would silently "
                "remove that evidence. Pass force=true to delete anyway (this also "
                "removes the links)."
            )
        for link_row in current_links:
            link_store.unlink(link_row["id"], conn=conn)
        receipt_store.delete_receipt(receipt_id, conn=conn)
    # Only after the transaction above committed — see ReceiptStore.delete_receipt's own
    # rollback-safety docstring for why the blob unlink cannot happen inside it.
    # unlink_all_blobs globs by id, not by a single derived path, so it also removes any
    # pre-2026-08-21 ".bin" extension-fix trail (revision 0045) — a plain blob_path().
    # unlink() only ever removed the current canonical file.
    receipt_store.unlink_all_blobs(receipt_id)
    return {
        "deleted": receipt_id,
        "unlinked_transaction_ids": [r["transaction_id"] for r in current_links],
    }


# ── Read helpers ────────────────────────────────────────────────────────────────


def receipts_for_transaction(transaction_id: str) -> list[dict]:
    """Every receipt linked to one transaction — the RFC item 26 read, consumed by
    ``services.ledger.get_transaction`` so both the REST detail route and the
    ``get_transactions`` AI tool present it automatically."""
    out = []
    for link in link_store.by_transaction(transaction_id):
        receipt = receipt_store.find_by_id(link["receipt_id"])
        if receipt is None:
            continue  # a dangling link (should not happen; never surface a ghost row)
        out.append(
            {
                "receipt_id": receipt["id"],
                "link_id": link["id"],
                "filename": receipt.get("filename"),
                "media_type": receipt.get("media_type"),
                "created_at": receipt.get("created_at"),
                "linked_at": link.get("linked_at"),
                "source_attachment_id": receipt.get("source_attachment_id"),
            }
        )
    return out


def transactions_for_receipt(receipt_id: str) -> list[str]:
    """Every transaction id one receipt supports — the receipt-delete confirmation read."""
    return [row["transaction_id"] for row in link_store.by_receipt(receipt_id)]
