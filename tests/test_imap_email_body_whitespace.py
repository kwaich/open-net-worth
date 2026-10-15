"""
Regression tests for excessive whitespace in fetched/parsed email bodies.

Before the fix, ``_extract_body``'s text/plain branch only called ``plain.strip()``
(an outer strip only) -- runs of blank lines and per-line trailing spaces inside the
body went straight into ``body_text`` unfiltered. Banking/template emails routinely
ship a plain-text part padded this way (a Meridian Bank card-alert email is the reproduction
case below), wasting LLM tokens and rendering ugly in the chat transcript.

``_normalize_whitespace`` strips each line and collapses runs of blank lines to at
most one -- but it is deliberately NOT called inside ``_extract_body`` itself.
``_extract_body``'s return value also feeds ``content_fallback_id`` (the stable
dedup identity for a genuinely-no-Message-ID email, computed in ``_parse_full_message``
in ``imap_client.py``), so it must stay byte-identical across a formatting-only change
like this one -- a headerless email refetched after this deploy must still hash to
the SAME fallback id as before, or it double-books. ``_parse_full_message`` therefore
computes the hash off the RAW body and only normalizes the separate ``body_text``
copy it stores/returns. The tests below pin both halves of that contract.
"""

from __future__ import annotations

from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from backend.core.email_ids import content_fallback_id
from backend.ingest import imap_client
from backend.ingest.imap_client import _extract_body, _normalize_whitespace

# A trimmed reproduction of a Meridian Bank card-transaction-alert email that
# motivated this fix -- the header block repeats three times with huge blank-line
# padding between each repeat, and most lines carry a trailing space.
_CARD_ALERT_PLAIN = (
    "Card Transaction Alert \n"
    "--> \n"
    "\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n"
    "Card Transaction Alert \n"
    "\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n"
    "  \n"
    "\n\n\n\n\n\n\n\n\n\n\n\n"
    "Card Transaction Alert \n"
    "\n\n\n\n\n\n\n\n\n\n\n\n"
    "Transaction Ref: SP0000000000000000000000 \n"
    "\n"
    "Dear Sir / Madam, \n"
    "\n"
    "We refer to your card transaction request dated 19/08/26. "
    "We are pleased to confirm that the transaction was completed. \n"
    "\n"
    "Date & Time: 19 AUG 14:22 (SGT) \n"
    "Amount: SGD68.20 \n"
    "From: Meridian Bank card ending 3021 \n"
    "To: GOOGLE*GOOGLE ONE SINGAPORE SGP \n"
    "\n"
    "If unauthorised, please login to Meridian Bank mobile to report fraud dispute "
    "immediately. Alternatively, call our Meridian Bank hotline.\n"
    "\n\n"
    "Thank you for banking with us. \n"
    "\n\n"
    "Yours faithfully, \n"
    "Meridian Bank Ltd \n"
    "\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n\n"
    "Please do not reply to this email as it is auto generated\n"
)


# ── _normalize_whitespace unit tests ────────────────────────────────────────


def test_normalize_whitespace_collapses_blank_line_runs_and_trims_trailing_spaces():
    raw = "a \nb  \n\n\n\n\nc\n\n\n"
    assert _normalize_whitespace(raw) == "a\nb\n\nc"


def test_normalize_whitespace_strips_leading_and_trailing_blank_lines():
    assert _normalize_whitespace("\n\n\nhello\n\n\n") == "hello"


def test_normalize_whitespace_preserves_single_blank_line_as_paragraph_break():
    assert _normalize_whitespace("para one\n\npara two") == "para one\n\npara two"


def test_normalize_whitespace_noop_on_already_clean_text():
    assert _normalize_whitespace("line one\nline two") == "line one\nline two"


# ── _extract_body: RAW, unnormalized -- this is deliberate, see module docstring ──


def test_extract_body_does_not_normalize_whitespace_itself():
    """`_extract_body` must return the raw body untouched by `_normalize_whitespace` --
    its output feeds `content_fallback_id`'s dedup hash, which must not depend on
    display formatting. Normalization is the CALLER's job (`_parse_full_message`
    applies it only to the separate `body_text` copy)."""
    msg = MIMEText(_CARD_ALERT_PLAIN, "plain")
    raw = _extract_body(msg)

    # The blank-line padding and trailing spaces from the source are still present.
    assert "\n\n\n" in raw
    assert raw != _normalize_whitespace(raw)


def test_extract_body_then_normalize_trims_real_world_padded_bank_alert_plain_text():
    msg = MIMEText(_CARD_ALERT_PLAIN, "plain")
    body = _normalize_whitespace(_extract_body(msg))

    # No run of blank lines survives -- at most one blank line between paragraphs.
    assert "\n\n\n" not in body
    # No trailing spaces survive on any line.
    assert all(line == line.rstrip() for line in body.splitlines())
    # The real content is intact.
    assert "Transaction Ref: SP0000000000000000000000" in body
    assert "Amount: SGD68.20" in body
    assert "Please do not reply to this email as it is auto generated" in body
    # Meaningfully smaller than the padded original (the blank-line padding is gone).
    assert len(body) < len(_CARD_ALERT_PLAIN) * 0.9


def test_extract_body_then_normalize_trims_html_derived_whitespace_too():
    html_body = (
        "<html><body>"
        "<table><tr><td>Card Transaction Alert</td></tr></table>\n"
        "\n\n\n\n\n"
        "<p>Amount: SGD15.90</p>\n"
        "\n\n\n\n\n\n\n"
        "<p>Thank you for banking with us.</p>"
        "</body></html>"
    )
    msg = MIMEMultipart("alternative")
    msg.attach(MIMEText(html_body, "html"))

    body = _normalize_whitespace(_extract_body(msg))

    assert "\n\n\n" not in body
    assert "Card Transaction Alert" in body
    assert "Amount: SGD15.90" in body
    assert all(line == line.rstrip() for line in body.splitlines())


# ── content_fallback_id stability against _extract_body/_normalize_whitespace ──


def test_content_fallback_id_is_computed_off_the_raw_pre_normalization_body():
    """Directly pins the contract: hashing the raw body vs. hashing the normalized
    body for the SAME email produces DIFFERENT ids -- proving `content_fallback_id`
    must be fed the raw value, never the display-trimmed one."""
    msg = MIMEText(_CARD_ALERT_PLAIN, "plain")
    raw = _extract_body(msg)
    normalized = _normalize_whitespace(raw)
    assert raw != normalized  # sanity: normalization actually changes this body

    id_from_raw = content_fallback_id("Card Transaction Alert", "Meridian Bank", "2026-08-19", raw)
    id_from_normalized = content_fallback_id(
        "Card Transaction Alert", "Meridian Bank", "2026-08-19", normalized
    )
    assert id_from_raw != id_from_normalized


# ── _parse_full_message: end-to-end through the actual parse+hash path ─────────
# `_parse_full_message(uid, raw_bytes)` is pure (no I/O) — the shared per-message parser
# behind every full-RFC822-fetch call site (fetch_by_message_ids, search_headers' own
# no-Message-ID fallback). Calling it directly is simpler and more direct than routing
# through a fake IMAP connection, and covers the identical code path.

_HEADERLESS_RAW_EMAIL = (
    "From: Meridian Bank <alerts@bank-a.example>\r\n"
    "Subject: Card Transaction Alert\r\n"
    "Date: Wed, 19 Aug 2026 14:22:00 +0800\r\n"
    "\r\n" + _CARD_ALERT_PLAIN
).encode()


def test_parse_full_message_fallback_id_survives_a_future_normalize_whitespace_change(
    monkeypatch,
):
    """End-to-end regression for the reviewer-requested guarantee: parsing the SAME
    headerless (no Message-ID) raw email content through `_parse_full_message` produces
    an IDENTICAL fallback `message_id` even if `_normalize_whitespace`'s formatting
    behaviour changes later -- only `body_text` (display) may drift, never the id.
    """
    baseline = imap_client._parse_full_message(1, _HEADERLESS_RAW_EMAIL)

    # Simulate a hypothetical FUTURE formatting change to the normalizer -- it now
    # does something wildly different from today's collapse-blank-lines behaviour.
    monkeypatch.setattr(
        imap_client, "_normalize_whitespace", lambda text: "COMPLETELY DIFFERENT FORMATTING"
    )
    reformatted = imap_client._parse_full_message(1, _HEADERLESS_RAW_EMAIL)

    assert reformatted["message_id"] == baseline["message_id"], (
        "the fallback id must not depend on _normalize_whitespace's output"
    )
    assert reformatted["body_text"] != baseline["body_text"], (
        "sanity check: the monkeypatched normalizer really did change body_text"
    )


def test_parse_full_message_body_text_is_normalized_but_message_id_uses_raw_body():
    msg = imap_client._parse_full_message(1, _HEADERLESS_RAW_EMAIL)

    # body_text is the trimmed/display copy.
    assert "\n\n\n" not in msg["body_text"]
    # The fallback id matches hashing the RAW (unnormalized) extracted body directly.
    expected_id = content_fallback_id(
        "Card Transaction Alert",
        "Meridian Bank <alerts@bank-a.example>",
        msg["date_time"] or msg["date"],
        _CARD_ALERT_PLAIN.strip()[:2000],
    )
    assert msg["message_id"] == expected_id
