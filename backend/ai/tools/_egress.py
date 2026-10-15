"""Egress control for model-chosen URLs.

`core.net_guard.assert_public_url` answers **where** a fetch may connect — it blocks private,
loopback, link-local and cloud-metadata targets. It deliberately permits any *public* host,
because that is the entire point of a web-fetch tool.

**What leaves with the request is NOT bounded.** A 256-char cap on query+fragment shipped for
security audit 2026-08-25 FINDING 1 (a `run_sql_query` → `fetch_page("…?d=<ledger>")` chain,
both ungated). It was removed by owner decision on 2026-10-05: signed capability links — a
Steam purchase receipt's `sparams` token runs ~900 chars — are exactly the URLs the email-scan
automation must read, unattended, so neither a refusal nor a confirm card works. The
exfiltration chain is an accepted risk recorded in `KNOWN_ISSUES.md`.
"""

from __future__ import annotations

from backend.core.net_guard import UnsafeUrlError, assert_public_url


def assert_safe_outbound_url(url: str) -> None:
    """Raise ``UnsafeUrlError`` unless ``url`` is safe to *reach* (the SSRF guard)."""
    assert_public_url(url)


__all__ = ["UnsafeUrlError", "assert_safe_outbound_url"]
