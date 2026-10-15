"""REST surface for transaction receipts (cross-channel-attachments RFC section 2.2/2.3).

Thin over the one write choke (``services.receipts.apply_receipt_change``) and its read
helper (``receipts_for_transaction``) — mirrors ``backend/ai/tools/receipts.py``'s request/
response shapes exactly, so the drawer's attach/unlink flow and the chat tool stay in sync.
No business logic here: validate input -> call ONE service -> shape response.

Only unlink and promote/attach-existing are exposed over REST — a receipt DELETE (permanent)
stays chat-tool-only (``delete_receipt``), per the UI spec's deliberate v1 scope.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel

from backend.api._blob_response import inline_blob_headers
from backend.api.upload import _MAX_UPLOAD_BYTES
from backend.core.errors import OpenNetWorthError, to_http_status
from backend.services.receipts import apply_receipt_change, receipt_store, receipts_for_transaction

router = APIRouter()


def _require_transaction(txn_id: str) -> None:
    from backend.services import ledger as ledger_service

    if ledger_service.get_transaction(txn_id) is None:
        raise HTTPException(404, "Transaction not found")


@router.get("/api/transactions/{txn_id}/receipts")
def list_transaction_receipts(txn_id: str):
    """Every receipt linked to one transaction — the drawer's Receipts section read."""
    _require_transaction(txn_id)
    return {"receipts": receipts_for_transaction(txn_id)}


class AttachReceiptBody(BaseModel):
    attachment_id: Optional[str] = None
    receipt_id: Optional[str] = None


@router.post("/api/transactions/{txn_id}/receipts")
def attach_receipt(txn_id: str, body: AttachReceiptBody):
    """Promote a chat attachment into a durable receipt and link it (``attachment_id``), or
    link an already-promoted receipt to another transaction it also supports (``receipt_id``).
    Mutually exclusive, mirroring the ``attach_receipt`` AI tool exactly."""
    attachment_id = (body.attachment_id or "").strip()
    receipt_id = (body.receipt_id or "").strip()
    if bool(attachment_id) == bool(receipt_id):
        raise HTTPException(422, "Provide exactly one of attachment_id or receipt_id.")
    try:
        if attachment_id:
            result = apply_receipt_change(
                {
                    "type": "promote_from_attachment",
                    "attachment_id": attachment_id,
                    "transaction_id": txn_id,
                }
            )
        else:
            result = apply_receipt_change(
                {"type": "attach_existing", "receipt_id": receipt_id, "transaction_id": txn_id}
            )
    except OpenNetWorthError as exc:
        raise HTTPException(to_http_status(exc), exc.message or str(exc)) from exc
    return result


@router.post("/api/transactions/{txn_id}/receipts/upload")
async def upload_receipt(txn_id: str, file: UploadFile):
    """Save a freshly-picked file directly as a receipt and link it — the drawer's own
    "+ Attach receipt" button. Bytes go straight to ReceiptStore, never through the generic
    chat-attachment parser (that parser exists to make a file usable by Lucius in chat —
    extract text, decode an image — none of which a receipt needs, and for a scanned PDF it
    used to mean the original bytes weren't even available to promote later). Distinct from
    ``attach_receipt`` above, which stays the right choke for linking a file that's already
    an attachment elsewhere in the conversation."""
    content = await file.read(_MAX_UPLOAD_BYTES + 1)
    if len(content) > _MAX_UPLOAD_BYTES:
        raise HTTPException(413, "file too large")
    try:
        result = apply_receipt_change(
            {
                "type": "upload",
                "content": content,
                "filename": file.filename or "",
                "media_type": file.content_type,
                "transaction_id": txn_id,
            }
        )
    except OpenNetWorthError as exc:
        raise HTTPException(to_http_status(exc), exc.message or str(exc)) from exc
    return result


@router.delete("/api/transactions/{txn_id}/receipts/{receipt_id}")
def unlink_receipt(txn_id: str, receipt_id: str):
    """Remove the link between one receipt and one transaction — never deletes the receipt
    itself (it may still support other transactions). Mirrors the ``unlink_receipt`` tool."""
    try:
        result = apply_receipt_change(
            {"type": "unlink", "transaction_id": txn_id, "receipt_id": receipt_id}
        )
    except OpenNetWorthError as exc:
        raise HTTPException(to_http_status(exc), exc.message or str(exc)) from exc
    return result


@router.get("/api/receipts/{receipt_id}/file")
def get_receipt_file(receipt_id: str):
    """Raw bytes for one receipt — the row thumbnail's direct <img> source and the in-app
    viewer's fetch target. 404 for an unknown id or a metadata row whose blob is missing on
    disk.

    ``media_type`` on the row is whatever the ORIGINAL upload declared, never sniffed — so it
    is untrusted. Only a fixed allowlist of real image/PDF types is ever served inline with
    their real content-type; anything else (a spoofed "text/html", an SVG, an unknown type)
    is forced to download as application/octet-stream instead, so a direct/top-level
    navigation to this URL can never render as a scriptable document on this origin. This is
    the same failure mode this file's family already guards against elsewhere (statements
    force ``attachment``; chat images are gated to ``kind == "image"`` at parse time) — this
    route is the one that needs its OWN gate because it is the first receipt route to serve
    ``inline`` at all."""
    row = receipt_store.find_by_id(receipt_id)
    if row is None:
        raise HTTPException(404, "Receipt not found")
    content = receipt_store.read_bytes(receipt_id)
    if content is None:
        raise HTTPException(404, "Receipt file missing on disk")
    filename = row.get("filename") or "receipt"
    effective_type, headers = inline_blob_headers(row.get("media_type"), filename)
    # A receipt's bytes never change once promoted (services/receipts.py's own note) — same
    # immutable-cache reasoning as GET /api/uploads/{ref_id}/image.
    headers["Cache-Control"] = "private, max-age=31536000, immutable"
    return Response(content=content, media_type=effective_type, headers=headers)
