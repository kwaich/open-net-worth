"""backend/api/_blob_response.py — the shared inline-vs-download safety gate for every route
that serves a caller-declared-media-type blob back to the browser."""

from __future__ import annotations

from backend.api._blob_response import content_disposition, inline_blob_headers, safe_media_type


def test_safe_media_type_allowlists_real_image_and_pdf_types():
    assert safe_media_type("image/jpeg") == ("image/jpeg", True)
    assert safe_media_type("application/pdf") == ("application/pdf", True)


def test_safe_media_type_case_and_parameter_insensitive():
    assert safe_media_type("IMAGE/JPEG") == ("image/jpeg", True)
    assert safe_media_type("image/jpeg; charset=binary") == ("image/jpeg", True)


def test_safe_media_type_refuses_svg_and_html():
    assert safe_media_type("image/svg+xml")[1] is False
    assert safe_media_type("text/html")[1] is False
    assert safe_media_type(None)[1] is False


def test_content_disposition_strips_crlf_and_quotes():
    header = content_disposition("inline", 'a\r\nSet-Cookie: evil=1"')
    assert "\r" not in header and "\n" not in header
    assert header.count('"') == 2  # exactly the ascii-fallback filename's own quote pair


def test_content_disposition_strips_control_characters():
    """Regression: a bare CR/LF/quote regex still let a NUL or vertical-tab through, which
    violates the HTTP header-value grammar under an h11-based server."""
    header = content_disposition("inline", "a\x00b\x0bc.jpg")
    assert "\x00" not in header
    assert "\x0b" not in header


def test_content_disposition_falls_back_when_filename_is_entirely_stripped():
    header = content_disposition("inline", '"""')
    assert 'filename="file"' in header


def test_content_disposition_caps_length():
    # The filename appears twice (ascii fallback + UTF-8 percent-encoded) plus header
    # boilerplate — bound generously above the 200-char cap applied to EACH copy, well below
    # an uncapped 500-char input.
    header = content_disposition("inline", "a" * 500 + ".jpg")
    assert len(header) < 500


def test_content_disposition_non_latin1_name_encodes_utf8():
    header = content_disposition("inline", "发票.pdf")
    assert "filename*=UTF-8''" in header


def test_inline_blob_headers_forces_download_for_non_allowlisted_type():
    media_type, headers = inline_blob_headers("text/html", "statement.csv")
    assert media_type == "application/octet-stream"
    assert "attachment" in headers["Content-Disposition"]
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert headers["Content-Security-Policy"] == "sandbox"


def test_inline_blob_headers_serves_real_image_inline():
    media_type, headers = inline_blob_headers("image/png", "receipt.png")
    assert media_type == "image/png"
    assert "inline" in headers["Content-Disposition"]
