"""backend/core/file_ext.py — the shared safe-extension helper every raw-file-backed
store (statements, receipts, attachments) derives its on-disk filename from (2026-08-21,
docs/adr/2026-08-21-unify-raw-file-storage-across-blob-stores.md). Extracted from
``backend/db/statement_store.py``'s own pre-existing ``_file_ext``, expanded to cover the
full set of kinds ``backend/services/attachment_parse.py`` produces.
"""

from __future__ import annotations

from backend.core.file_ext import file_ext


def test_filename_suffix_wins_over_mime():
    assert file_ext("statement.pdf", "text/csv") == ".pdf"


def test_every_original_statement_kind_still_maps_the_same():
    """Behavior-preserving for statement_store.py's original four groups."""
    cases = [
        ("bank.pdf", "application/pdf", ".pdf"),
        ("bank.csv", "text/csv", ".csv"),
        ("bank.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", ".xlsx"),
        ("bank.xls", "application/vnd.ms-excel", ".xls"),
        ("photo.png", "image/png", ".png"),
        ("photo.jpg", "image/jpeg", ".jpg"),
        ("photo.jpeg", "image/jpeg", ".jpeg"),
        ("photo.gif", "image/gif", ".gif"),
        ("photo.webp", "image/webp", ".webp"),
    ]
    for filename, mime, expected in cases:
        assert file_ext(filename, mime) == expected, filename


def test_structured_statement_formats():
    for ext in (".ofx", ".qfx", ".qbo", ".qif", ".mt940", ".sta", ".camt"):
        assert file_ext(f"statement{ext}", "application/octet-stream") == ext


def test_plain_text():
    assert file_ext("notes.txt", "text/plain") == ".txt"
    assert file_ext("", "text/plain") == ".txt"  # mime fallback, no usable filename


def test_broader_image_types_including_svg():
    """An attachment upload accepts ANY image/* sub-mime, not just the original four raster
    types — a real image/svg+xml upload must still get a safe, non-executable extension."""
    for filename, mime, expected in (
        ("logo.svg", "image/svg+xml", ".svg"),
        ("scan.bmp", "image/bmp", ".bmp"),
        ("scan.tif", "image/tiff", ".tif"),
        ("scan.tiff", "image/tiff", ".tiff"),
        ("photo.heic", "image/heic", ".heic"),
        ("photo.heif", "image/heif", ".heif"),
    ):
        assert file_ext(filename, mime) == expected


def test_mime_fallback_when_filename_has_no_recognized_suffix():
    assert file_ext("upload", "image/svg+xml") == ".svg"
    assert file_ext("upload", "application/pdf") == ".pdf"
    assert file_ext(None, "image/png") == ".png"


def test_unknown_type_falls_back_to_bin_not_anything_else():
    assert file_ext("mystery.xyz", "application/x-mystery") == ".bin"
    assert file_ext("", "") == ".bin"
    assert file_ext(None, None) == ".bin"


def test_never_trusts_a_hostile_filename_beyond_its_suffix():
    """A path-traversal-shaped filename must never influence anything beyond the bounded
    extension it happens to end with — this IS the security invariant this helper exists
    to satisfy for every store built on it."""
    assert file_ext("../../etc/passwd", "text/csv") == ".csv"
    assert file_ext("../../etc/passwd.png", "text/csv") == ".png"
    assert file_ext("script.py; rm -rf /", "application/octet-stream") == ".bin"
