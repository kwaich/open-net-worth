"""A model-chosen URL is checked for WHERE it connects, not for how much it carries.

The 256-char query+fragment cap (security audit 2026-08-25, FINDING 1) was removed on
2026-10-05 by owner decision: it refused signed capability links such as a Steam receipt's
~900-char `sparams` token, which the unattended email-scan automation must read. The
exfiltration risk it bounded is an accepted known issue (`KNOWN_ISSUES.md`). The SSRF guard
is unaffected.
"""

from __future__ import annotations

import pytest

from backend.ai.tools._egress import UnsafeUrlError, assert_safe_outbound_url


def test_a_long_signed_receipt_link_is_allowed(monkeypatch: pytest.MonkeyPatch) -> None:
    """The scar: a Steam PurchaseReceipt link (921 chars of query) was refused outright."""
    monkeypatch.setattr("backend.ai.tools._egress.assert_public_url", lambda _url: None)
    assert_safe_outbound_url(
        "https://store.steampowered.com/email/PurchaseReceipt?sparams="
        + "e" * 850
        + "&check="
        + "a" * 64
    )


def test_the_ssrf_guard_still_runs() -> None:
    with pytest.raises(UnsafeUrlError):
        assert_safe_outbound_url("http://169.254.169.254/latest/meta-data/")
