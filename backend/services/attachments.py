"""Durable cross-channel chat attachments — the service every consumer goes through.

Replaces ``services/uploads.py`` (renamed alongside ``UploadStore`` -> ``AttachmentStore``,
revision 0041 / ADR ``2026-08-16-attachment-store-replaces-upload-store``). Attachments
persist until the user deletes them (Settings → Uploaded files), surviving restarts and
--reload — the live incident this originally fixed was a Retry after a hot reload silently
losing the attached statement, with the model then answering blind.

Low-level CRUD lives here; ``services.attachment_ingress.persist_inbound_file`` is the ONE
shared operation that VALIDATES + PERSISTS an inbound channel file before turn construction
(RFC item 11) — every channel (web upload, Telegram photo, Telegram document) calls that,
not this module's ``create_attachment`` directly.

Consumers:
  - ``services.attachment_ingress`` — the shared ingress choke, create at upload time
  - ``ai/attachments.py`` — resolve refs into the model turn (+ ``mark_used`` provenance)
  - ``ai/tools`` import/statement paths — ``get_attachment_import_source``
  - ``api/upload.py`` REST — list/delete for the Settings panel

A file the user marked Private in the Vault keeps its CONTENTS away from every model: a path
that hands contents to a model reads through ``get_attachment_for_model`` (or
``get_attachment_import_source``), which answer None for it — fail closed. ``get_attachment``
itself stays unguarded for the user's own views (chat thumbnails).

A small in-process LRU keeps repeat reads of multi-MB payloads (every chat turn re-resolves
the conversation's refs) off the disk hot path; deletes invalidate.
"""

from __future__ import annotations

import logging
import threading
from collections import OrderedDict
from typing import Optional

from backend.db.attachment_store import AttachmentStore

log = logging.getLogger(__name__)

_CACHE_MAX = 8
_cache: "OrderedDict[str, dict]" = OrderedDict()
_cache_lock = threading.Lock()


def _cache_put(ref_id: str, entry: dict) -> None:
    with _cache_lock:
        _cache[ref_id] = entry
        _cache.move_to_end(ref_id)
        while len(_cache) > _CACHE_MAX:
            _cache.popitem(last=False)


def _cache_drop(ref_id: Optional[str] = None) -> None:
    with _cache_lock:
        if ref_id is None:
            _cache.clear()
        else:
            _cache.pop(ref_id, None)


def create_attachment(
    entry: dict,
    content: bytes | None = None,
    filename: str = "",
    *,
    media_type: str | None = None,
    content_hash: str | None = None,
    source_channel: str = "",
    source_message_id: str | None = None,
    source_conversation_id: str | None = None,
    metadata: dict | None = None,
) -> str:
    """Persist a parsed attachment entry + its raw bytes. Returns its attachment_id.

    ``content`` is the raw bytes written verbatim to disk (see ``AttachmentStore.create``'s
    docstring for the full contract, including its ``None``-derives-from-``entry``
    fallback). Low-level — prefer ``services.attachment_ingress.persist_inbound_file`` at
    an actual channel boundary; this is also the direct call used by tests and by anything
    that already has a parsed entry in hand."""
    store = AttachmentStore()
    ref_id = store.create(
        entry,
        content,
        filename=filename,
        media_type=media_type,
        content_hash=content_hash,
        source_channel=source_channel,
        source_message_id=source_message_id,
        source_conversation_id=source_conversation_id,
        metadata=metadata,
    )
    # Cache exactly what a fresh read_payload() would return — not the caller's own `entry`
    # verbatim, which can disagree with the disk-reconstructed shape when `content` was
    # omitted and derived internally (e.g. a legacy .xls entry with neither `content` nor
    # `data`, where the derived raw bytes are b"" but `entry` itself may still say
    # content=None) — so a cache hit and a cache miss are ALWAYS byte-identical.
    cached = store.read_payload(ref_id)
    if cached is not None:
        _cache_put(ref_id, cached)
    return ref_id


def get_attachment(ref_id: str) -> Optional[dict]:
    """The full parsed entry, or None (unknown / deleted / payload missing)."""
    if not ref_id:
        return None
    with _cache_lock:
        hit = _cache.get(ref_id)
        if hit is not None:
            _cache.move_to_end(ref_id)
            return hit
    entry = AttachmentStore().read_payload(ref_id)
    if entry is not None:
        _cache_put(ref_id, entry)
    return entry


def private_refusal(ref_id: str) -> Optional[str]:
    """What to tell the model instead of a Private file's contents, or None when it is not
    Private. Asked by every path that hands attachment CONTENTS to a model."""
    from backend.services.vault import PRIVATE_REFUSAL, is_private_attachment

    return PRIVATE_REFUSAL if is_private_attachment(ref_id) else None


def get_attachment_for_model(ref_id: str) -> Optional[dict]:
    """``get_attachment`` for a path that hands the contents to a MODEL — None for a file
    marked Private too. Callers that need to say WHY ask ``private_refusal`` first."""
    if private_refusal(ref_id):
        return None
    return get_attachment(ref_id)


def get_attachment_meta(ref_id: str) -> Optional[dict]:
    """The metadata ROW only (filename/kind/media_type/created_at/...) — no payload read.
    Cheap existence check used to build a durable ``attachment_ref`` transcript block."""
    if not ref_id:
        return None
    return AttachmentStore().find_meta(ref_id)


def existing_attachment_ids(ref_ids) -> set[str]:
    """Which of `ref_ids` still exist — one batched lookup (a caller projecting many rows)."""
    return AttachmentStore().existing_ids(ref_ids)


def attachment_file_meta(ref_ids) -> dict[str, dict]:
    """The ones of `ref_ids` that still exist, each with its filename, media type and size —
    one batched lookup, for a list that shows what each file is."""
    return AttachmentStore().file_meta(ref_ids)


def read_attachment_bytes(ref_id: str) -> Optional[bytes]:
    """The ORIGINAL uploaded file's raw bytes, or None (unknown id / blob missing on disk).
    Used by ``GET /api/uploads/{ref_id}/file`` — bypasses the parsed-entry shape/cache
    entirely (no base64, no derived text), mirroring ``services.receipts.receipt_store.
    read_bytes``."""
    if not ref_id:
        return None
    return AttachmentStore().read_bytes(ref_id)


def get_attachment_import_source(ref_id: str) -> Optional[tuple[bytes, str]]:
    """``(raw_bytes, detected_format)`` for an importable attachment, or None (also for a
    Private one — every caller hands the parsed result to a model)."""
    entry = get_attachment_for_model(ref_id)
    if not entry:
        return None
    fmt = entry.get("format")
    content = entry.get("content")
    if not fmt or not isinstance(content, (bytes, bytearray)):
        return None
    return bytes(content), fmt


def mark_used(ref_id: str, session_id: Optional[str]) -> None:
    """Best-effort last-use provenance stamp — never raises into a chat turn."""
    try:
        AttachmentStore().mark_used(ref_id, session_id)
    except Exception:
        log.debug("attachments: mark_used failed for %s", ref_id, exc_info=True)


def list_attachments() -> list[dict]:
    """Metadata rows, newest first (no payloads)."""
    return AttachmentStore().list_all()


def attachments_for_message(session_id: Optional[str], message_id: Optional[str]) -> list[dict]:
    """Every attachment stamped with this exact ``(source_conversation_id, source_message_id)``
    pair — metadata rows only, no payloads. Lets a caller (transcript rehydration on reload)
    find attachments a TOOL result produced by provenance, rather than by parsing a marker back
    out of the tool result's own text (which for an external-MCP result is untrusted content we
    deliberately never mix our own machine-readable state into). Empty for a blank id — no
    accidental "every attachment with no message id" match."""
    if not session_id or not message_id:
        return []
    return [
        a
        for a in AttachmentStore().list_all()
        if a.get("source_conversation_id") == session_id
        and a.get("source_message_id") == message_id
    ]


def delete_attachment(ref_id: str) -> bool:
    """Delete one attachment (metadata + payload). True if it existed."""
    _cache_drop(ref_id)
    return AttachmentStore().delete_attachment(ref_id)


def in_vault(row: Optional[dict]) -> bool:
    """True when the user keeps this file in the Vault — the one place it is removed from."""
    return bool(((row or {}).get("vault") or {}).get("saved"))


def in_documents(row: Optional[dict]) -> bool:
    """True when Documents lists this file: kept there, or attached to an owned asset (it is
    removed on the asset's page)."""
    from backend.services.vault_assets import is_asset_upload

    return in_vault(row) or is_asset_upload(row)


def delete_all_attachments() -> int:
    """Delete every stored attachment EXCEPT files Documents lists — kept there or attached
    to an asset — whose removal is a Documents or asset action. Returns the number removed."""
    store = AttachmentStore()
    rows = [row for row in store.list_all() if not in_documents(row)]
    for row in rows:
        store.delete_attachment(row["id"])
    _cache_drop()
    return len(rows)
