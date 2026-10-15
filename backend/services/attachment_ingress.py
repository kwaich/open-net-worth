"""The ONE shared operation that validates + persists an inbound channel file before turn
construction (cross-channel-attachments RFC item 11).

Before this module, web upload (``api/upload.py``) and Telegram documents
(``connectors/telegram/handlers.py::handle_document``) each hand-rolled the same
parse → best-effort StatementStore.create → persist-attachment sequence, and Telegram
PHOTOS skipped attachment persistence entirely — surviving only as inline base64 blocks in
the transcript (the RFC's core complaint: "Telegram images are not durable attachment
references"). Every channel now calls ``persist_inbound_file`` here instead, so a photo,
a document, and a web upload all become the SAME kind of durable, retrievable record
regardless of which channel supplied them.

RFC Phase 4 (item 21, ``STATEMENT-ATTACHMENT-BOUNDARY``): this function used to ALSO write
every inbound file into ``StatementStore`` unconditionally — the RFC's core complaint that
"generic chat uploads are also written into statement history" (a screenshot or receipt
photo became a "statement" record just by being uploaded through chat). That call is
REMOVED. A generic attachment upload now creates ONLY an ``AttachmentStore`` row. Statement
creation is an explicit, separate action — ``services.statements.decisions.decision_preview``
(reached via the ``import_transactions`` tool or the Settings → Data import UI), which
accepts ``attachment_ref``/``source_attachment_id`` provenance and dedups by content hash —
see ``docs/adr/2026-08-15-separate-attachments-receipts-and-statements.md`` and
``.codex/rfcs/cross-channel-attachments-vision-and-statement-evidence.md`` section 2.3.
"""

from __future__ import annotations

import base64
import hashlib
import io
import logging

import pillow_heif
from PIL import Image

from backend.services.attachment_parse import parse_upload
from backend.services.attachments import create_attachment

# Registered once at import time (pillow-heif's own documented usage) rather than per call —
# idempotent, and every inbound file passes through this module regardless of channel.
pillow_heif.register_heif_opener()

log = logging.getLogger(__name__)

_HEIC_MIME_TYPES = frozenset({"image/heic", "image/heif"})


def _normalize_image(filename: str, mime: str, content: bytes) -> tuple[str, str, bytes]:
    """HEIC/HEIF — the default iPhone photo format — isn't in ``_blob_response``'s
    ``INLINE_SAFE_MEDIA_TYPES`` allowlist (forced to download rather than display), and no
    browser besides Safari can decode it inline even where that's bypassed. Transcode to
    JPEG HERE, the one choke every inbound image passes through regardless of channel, so
    the stored ``media_type``, the on-disk extension, and the served ``Content-Type`` are
    already a universally-renderable format — no special-casing needed anywhere downstream
    (``GET /api/uploads/{ref}/image``, the Assets tab thumbnail, a chat bubble's image).

    Every other image type (already-renderable) passes through completely unchanged — this
    is NOT a blanket "convert every upload to JPEG": that would drop a PNG's transparency
    and flatten an animated GIF to one frame for formats that display fine as-is.

    Best-effort: a corrupt file or a decode failure falls back to storing the ORIGINAL
    bytes/type rather than failing the whole upload — the known display gap this closes is
    strictly better than an upload that errors outright."""
    if (mime or "").lower() not in _HEIC_MIME_TYPES:
        return filename, mime, content
    try:
        image = Image.open(io.BytesIO(content))
        buf = io.BytesIO()
        image.convert("RGB").save(buf, format="JPEG", quality=88)
        new_content = buf.getvalue()
    except Exception:
        log.warning(
            "HEIC/HEIF -> JPEG conversion failed for %r; storing the original format",
            filename,
            exc_info=True,
        )
        return filename, mime, content
    stem = filename.rsplit(".", 1)[0] if "." in filename else filename
    return f"{stem}.jpg", "image/jpeg", new_content


def _attachment_raw_bytes(entry: dict, original_content: bytes) -> bytes:
    """The bytes to persist as this attachment's raw file (``AttachmentStore.create``'s
    ``content``) — the SAME bytes ``parse_upload``'s entry already carried before
    2026-08-21 (base64 ``data`` for an image, raw ``content`` for a text/structured/
    pdf-with-text/pdf_pages kind — for an encrypted Office file this is the DECRYPTED
    workbook, matching what ``get_attachment_import_source`` has always needed for a later
    re-import with no second password prompt). Falls back to ``original_content`` — the
    literal pre-parse upload bytes — only when the entry carries NEITHER (kind="text" from
    a legacy ``.xls`` file, the one case ``parse_upload`` never stashes any raw bytes for
    at all): a strict improvement (that kind previously had no raw copy anywhere), not a
    behavior change for any kind that already carried one."""
    content = entry.get("content")
    if isinstance(content, (bytes, bytearray)):
        return bytes(content)
    data_b64 = entry.get("data")
    if isinstance(data_b64, str) and data_b64:
        return base64.b64decode(data_b64)
    return original_content


def persist_inbound_file(
    filename: str,
    mime: str,
    content: bytes,
    *,
    source_channel: str,
    source_conversation_id: str | None = None,
    source_message_id: str | None = None,
    password: str | None = None,
) -> dict:
    """Parse + persist one inbound file from any chat channel.

    Returns ``{"attachment_id", "kind", "summary", "media_type", "was_encrypted"}``.
    Raises ``ValueError`` for an unsupported or unreadable file (including the
    ``"password_required: ..."`` sentinel an encrypted Office file raises, and
    ``PasswordProtectedPdfError`` for a PDF that needs a password) — unchanged from
    ``parse_upload``'s existing contract, so every caller's existing except-ValueError
    handling keeps working unmodified.

    ``source_channel`` is the RFC's provenance tag (``"chat:web"``, ``"connector:telegram"``);
    ``source_conversation_id`` is the session id when the caller already has one bound (the
    web upload endpoint runs before a brand-new conversation's session necessarily exists,
    so it may legitimately be empty — ``ai.attachments.mark_used`` is what actually binds an
    attachment to the conversation that consumes it, on the same turn).

    Creates NO statement record — see module docstring. A caller that wants this file
    imported as a statement calls the explicit import path with this call's returned
    ``attachment_id`` as ``attachment_ref``.
    """
    # Normalized BEFORE content_hash/parse_upload run, so every downstream value — the hash,
    # the parsed entry's media_type, the stored filename/extension — already reflects what
    # actually gets persisted, with no special-casing needed past this one line.
    filename, mime, content = _normalize_image(filename, mime, content)
    content_hash = hashlib.sha256(content).hexdigest()
    entry = parse_upload(filename, mime, content, password=password)

    attachment_id = create_attachment(
        entry,
        _attachment_raw_bytes(entry, content),
        filename=filename,
        media_type=mime or entry.get("media_type"),
        content_hash=content_hash,
        source_channel=source_channel,
        source_message_id=source_message_id,
        source_conversation_id=source_conversation_id or None,
    )
    return {
        "attachment_id": attachment_id,
        "kind": entry["kind"],
        "summary": entry["summary"],
        "media_type": mime or entry.get("media_type"),
        "was_encrypted": bool(entry.get("was_encrypted")),
    }
