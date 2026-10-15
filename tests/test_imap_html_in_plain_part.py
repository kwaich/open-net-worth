"""
Regression: a sender that ships an HTML document as its text/plain part (kestreleats order
receipts) stored raw markup as ``body_text`` — the drawer showed ``<!DOCTYPE html …`` and the
model read 8000 chars of table attributes, truncated before the order total ever appeared.

``body_text`` now strips a markup-shaped plain part like the HTML fallback does, BEFORE the
length cap. The content-fallback dedup hash still reads ``_extract_body``'s raw value, so a
headerless email refetched after this fix keeps its id.
"""

from __future__ import annotations

from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from backend.core.email_ids import content_fallback_id
from backend.ingest import imap_client
from backend.ingest.imap_client import _extract_body, _parse_full_message

_RECEIPT_HTML = (
    '<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.0 Transitional//EN">'
    '<html dir="ltr"><head><title>Receipt</title>'
    "<style>td { font-family: Arial; }</style></head><body>"
    '<table align="center" width="100%" style="max-width:700px">'
    "<tr><td>Thank you for your order!</td></tr>"
    "<tr><td>Total</td><td>S$ 23.40</td></tr></table></body></html>"
)


def _headers(msg, *, message_id: bool = True):
    msg["Subject"] = "Thank you for your order!"
    msg["From"] = "Sample Food <no-reply@example.test>"
    msg["Date"] = "Fri, 09 Oct 2026 16:10:00 +0800"
    if message_id:
        msg["Message-ID"] = "<receipt-1@example.test>"
    return msg


def test_html_document_in_plain_part_is_stripped_to_text():
    msg = MIMEMultipart("alternative")
    msg.attach(MIMEText(_RECEIPT_HTML, "plain"))
    msg.attach(MIMEText(_RECEIPT_HTML, "html"))
    body = _parse_full_message(1, _headers(msg).as_bytes())["body_text"]
    assert "<" not in body
    assert "td {" not in body
    assert "Thank you for your order!" in body
    assert "S$ 23.40" in body


def test_single_part_plain_html_is_stripped_too():
    msg = _headers(MIMEText(_RECEIPT_HTML, "plain"))
    body = _parse_full_message(1, msg.as_bytes())["body_text"]
    assert "<" not in body
    assert "Thank you for your order!" in body


def test_markup_is_stripped_before_the_length_cap():
    padding = '<td style="padding:0;border:0;"></td>' * 400
    assert len(padding) > imap_client._MAX_BODY_CHARS
    html_doc = _RECEIPT_HTML.replace("<body>", "<body>" + padding)
    msg = _headers(MIMEText(html_doc, "plain"))
    body = _parse_full_message(1, msg.as_bytes())["body_text"]
    assert "S$ 23.40" in body


def test_real_plain_text_with_angle_brackets_is_left_alone():
    text = "Paid S$ 5 < limit\nReply <here> to stop"
    msg = _headers(MIMEText(text, "plain"))
    assert _parse_full_message(1, msg.as_bytes())["body_text"] == text


def test_headerless_fallback_id_still_hashes_the_raw_body():
    msg = _headers(MIMEText(_RECEIPT_HTML, "plain"), message_id=False)
    parsed = _parse_full_message(1, msg.as_bytes())
    raw = _extract_body(msg)
    assert raw.startswith("<!DOCTYPE")
    assert parsed["message_id"] == content_fallback_id(
        parsed["subject"], parsed["from"], parsed["date_time"], raw
    )
