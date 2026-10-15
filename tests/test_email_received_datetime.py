"""Regression tests for the EM1 `date_time` field on `imap_client._parse_full_message`.

`_parse_full_message` already normalizes the RFC 5322 Date header to a plain
`YYYY-MM-DD` `date` field (unchanged — other consumers depend on that shape). This adds
an ADDITIVE `date_time` field: the same Date header parsed via `parsedate_to_datetime`,
serialized as a full ISO 8601 string preserving its own UTC offset — so the EM1
"Received" provenance field can carry hour+minute instead of date-only. A malformed/
missing Date header falls back to `date_time=None` (never a fabricated time).

Hermetic and direct — `_parse_full_message(uid, raw_bytes)` is pure (no I/O), the shared
per-message parser behind every full-RFC822-fetch call site, so no fake IMAP connection
is needed to exercise it.
"""

from __future__ import annotations

from backend.ingest import imap_client


def test_parse_full_message_date_time_carries_hour_minute_and_offset():
    """A well-formed Date header with an explicit offset yields a full ISO date_time —
    additive alongside the unchanged date-only `date` field."""
    raw = (
        b"From: bank@example.com\r\n"
        b"Subject: Statement ready\r\n"
        b"Date: Sat, 06 Jun 2026 14:37:00 +0800\r\n"
        b"Message-ID: <m-1@x>\r\n"
        b"\r\n"
        b"Charged SGD 12.34 to your card."
    )
    msg = imap_client._parse_full_message(1, raw)

    assert msg["date"] == "2026-06-06"
    assert msg["date_time"] == "2026-06-06T14:37:00+08:00"


def test_parse_full_message_date_time_none_on_malformed_date_header():
    """A malformed/unparseable Date header falls back to date_time=None (never a
    fabricated time) while `date` still falls back to today's date, unchanged."""
    raw = (
        b"From: bank@example.com\r\n"
        b"Subject: Statement ready\r\n"
        b"Date: not-a-real-date\r\n"
        b"Message-ID: <m-2@x>\r\n"
        b"\r\n"
        b"Charged SGD 12.34 to your card."
    )
    msg = imap_client._parse_full_message(1, raw)

    assert msg["date_time"] is None
