"""A scanned (image-only) PDF attached in chat is importable, and a refusal tells the truth.

Owner QA, 2026-10-12: a real card statement whose pages are pictures (no text layer) went in
through chat. The upload labelled a text PDF ``format: "pdf"`` but left a scan unlabelled — a
June rule from when the importer refused scans, never revisited after it learned to read them
through its vision tier (Accounts → Import already did, via ``detect_format``). The import tool
then refused with "the chat upload cache expires after 30 minutes", a cache retired on 17 July,
and Lucius hand-typed 59 rows outside an import batch instead.
"""

from __future__ import annotations

import fitz
import pytest

from backend.ai.tools import imports as import_tools
from backend.services.attachment_parse import parse_upload


def _image_only_pdf() -> bytes:
    """One page carrying a picture and no text — what a scanned statement is."""
    doc = fitz.open()
    page = doc.new_page(width=300, height=200)
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 40, 20), 0)
    pix.clear_with(200)
    page.insert_image(fitz.Rect(10, 10, 290, 190), pixmap=pix)
    data = doc.tobytes()
    doc.close()
    return data


def test_a_scanned_pdf_upload_is_labelled_importable_like_a_text_pdf():
    content = _image_only_pdf()
    entry = parse_upload("statement.pdf", "application/pdf", content)
    assert entry["kind"] == "pdf_pages"  # read as page images: there is no text to extract
    assert entry["format"] == "pdf"  # ...and still importable, through the vision tier
    assert entry["content"] == content


def test_an_attachment_that_is_gone_says_so(monkeypatch):
    monkeypatch.setattr("backend.services.attachments.get_attachment_meta", lambda ref: None)
    message = import_tools._unimportable_attachment("att_missing")
    assert "No attachment with that ref" in message
    assert "30 min" not in message


def test_an_attachment_that_is_not_a_statement_says_what_it_is(monkeypatch):
    monkeypatch.setattr(
        "backend.services.attachments.get_attachment_meta",
        lambda ref: {"filename": "receipt-photo.jpg"},
    )
    message = import_tools._unimportable_attachment("att_photo")
    assert message.startswith("receipt-photo.jpg isn't a statement file the importer reads")
    assert "expire" not in message


@pytest.mark.parametrize("tool", ["import_transactions", "import_8b", "diff_statement"])
def test_no_tool_still_claims_attachments_expire(tool):
    """Attachments are durable until the user deletes them; a description promising expiry
    sends Lucius off to re-fetch or hand-enter what he could simply import."""
    import json

    import backend.ai.tools  # noqa: F401 — registry fill
    from backend.ai.tools.registry import build_tool_schemas

    schema = next(s for s in build_tool_schemas() if s["name"] == tool)
    text = json.dumps(schema).lower()
    assert "30 min" not in text and "cache has expired" not in text
