"""Page images of a PDF in Documents, so the panel shows the document itself.

The stores' own file routes can't do this: a statement is only ever served as a download, and
a browser's built-in PDF viewer is unreliable inside a side panel and on a phone (the upload
route's ``Content-Security-Policy: sandbox`` also keeps it from running there). Drawing one page
at a time as a PNG on the server shows the same thing everywhere — and a PNG carries no script.
The user's own view: Private does not apply (it keeps contents from Lucius, not from the user).
"""

from __future__ import annotations

from backend.core.errors import NotFoundError, ValidationError
from backend.db.receipt_store import ReceiptStore
from backend.db.statement_store import StatementStore
from backend.services import vault

_TARGET_WIDTH_PX = 1200  # sharp in a 480px panel on a 2x screen, and on a phone
_MAX_HEIGHT_PX = 2400  # a long receipt page is scaled down to fit, not drawn huge
_MIN_ZOOM, _MAX_ZOOM = 0.5, 3.0


def is_pdf(item: dict) -> bool:
    media_type = (item.get("media_type") or "").lower()
    return media_type == "application/pdf" or (item.get("filename") or "").lower().endswith(".pdf")


def original_bytes(item: dict) -> bytes | None:
    kind, source_id = item["kind"], item["source_id"]
    if kind in ("file", "asset"):
        from backend.services.attachments import read_attachment_bytes

        return read_attachment_bytes(source_id)
    if kind == "statement":
        return StatementStore().read_file_bytes(source_id)
    if kind == "receipt":
        return ReceiptStore().read_bytes(source_id)
    return None


def page_image(vid: str, page: int) -> tuple[bytes, int]:
    """``(png_bytes, page_count)`` for page ``page`` (1-based) of a PDF item."""
    import fitz  # pymupdf — the same renderer the upload parser uses

    item = vault.get_item(vid)
    if not is_pdf(item):
        raise ValidationError("only a PDF has page previews")
    content = original_bytes(item)
    if not content:
        raise NotFoundError("the file is missing")
    try:
        doc = fitz.open(stream=content, filetype="pdf")
    except Exception as exc:
        raise ValidationError("this PDF can't be read — download it instead") from exc
    try:
        if doc.needs_pass:
            raise ValidationError("this PDF is password-protected — download it to open it")
        count = doc.page_count
        if not 1 <= page <= count:
            raise NotFoundError(f"no page {page} — this PDF has {count}")
        rect = doc[page - 1].rect
        zoom = min(_TARGET_WIDTH_PX / rect.width, _MAX_HEIGHT_PX / rect.height)
        zoom = max(_MIN_ZOOM, min(_MAX_ZOOM, zoom))
        pix = doc[page - 1].get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
        return pix.tobytes("png"), count
    finally:
        doc.close()


_THUMB_PX = 160  # a 36px list tile on a 3x phone, with room to spare


def is_image(item: dict) -> bool:
    return (item.get("media_type") or "").lower().startswith("image/")


def thumbnail(vid: str) -> bytes:
    """A small JPEG of a photo item for the list — never the full-size original, which a
    phone would otherwise fetch once per row."""
    import io

    from PIL import Image, ImageOps

    item = vault.get_item(vid)
    if not is_image(item):
        raise ValidationError("only a photo has a thumbnail")
    content = original_bytes(item)
    if not content:
        raise NotFoundError("the file is missing")
    try:
        with Image.open(io.BytesIO(content)) as img:
            small = ImageOps.exif_transpose(img).convert("RGB")
            small.thumbnail((_THUMB_PX, _THUMB_PX))
            out = io.BytesIO()
            small.save(out, format="JPEG", quality=80)
            return out.getvalue()
    except Exception as exc:  # a corrupt or unsupported image: the row keeps its icon
        raise ValidationError("this photo can't be read") from exc
