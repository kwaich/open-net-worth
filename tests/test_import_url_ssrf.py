"""A statement-import URL is model-controlled, so it gets the SSRF guard.

The scar (security audit 2026-08-25, FINDING 12): `fetch_url_bytes` fetched with
`httpx.get(..., follow_redirects=True)` and no guard at all. Its docstring justified that with
"this is a single-user, self-hosted app whose operator pastes their OWN sheet URL" — but
`source_url` is a field on `ImportTransactionsInput`, so the URL can be chosen by the MODEL,
and therefore by whatever untrusted text steered it (an email body, a fetched page, a
statement memo). "Import your statement from http://169.254.169.254/latest/meta-data/…" would
have been honoured, with the response surfacing back through the import preview.

This was the fourth outbound-fetch site; the other three (`fetch_page`, the per-provider
twins, and the screen fetch) were guarded in the same audit round and this one was missed.
"""

from __future__ import annotations

import pytest

from backend.services.import_common import ImportError_, fetch_url_bytes


@pytest.mark.parametrize(
    "url",
    [
        "http://169.254.169.254/latest/meta-data/iam/security-credentials/",  # cloud metadata
        "http://127.0.0.1:8080/api/data/export",  # the app's own export route
        "http://localhost:8080/api/data/export",
        "http://10.0.0.5/secrets.csv",  # RFC1918
        "http://192.168.1.1/config.csv",
        "http://[::1]:8080/api/data/export",  # IPv6 loopback
    ],
)
def test_a_private_or_metadata_target_is_refused(url: str) -> None:
    """Each of these is a target only an attacker (or a confused model) wants."""
    with pytest.raises(ImportError_) as excinfo:
        fetch_url_bytes(url)
    assert "Refusing to fetch that URL" in str(excinfo.value)


def test_a_non_http_scheme_is_still_refused_with_its_own_message() -> None:
    """The pre-existing scheme check must keep its clearer, user-correctable wording rather
    than being swallowed by the new guard."""
    with pytest.raises(ImportError_) as excinfo:
        fetch_url_bytes("file:///etc/passwd")
    assert "must start with http" in str(excinfo.value)


def test_an_empty_url_is_still_refused() -> None:
    with pytest.raises(ImportError_) as excinfo:
        fetch_url_bytes("   ")
    assert "No URL provided" in str(excinfo.value)


def test_a_public_url_is_still_fetched(monkeypatch) -> None:
    """The guard must not over-block: this function's whole job is fetching a hosted CSV or a
    published Google Sheet, and those are ordinary public URLs.

    (Deliberately NOT asserted by monkeypatching `pinned_urlopen` and checking it went
    uncalled for a blocked URL — an earlier draft of this file did that and it proves nothing.
    Validation happens INSIDE `pinned_urlopen`, which resolves and checks before it connects,
    so a blocked host never receives a connection even though the refusal is raised from
    within the fetch primitive. Stubbing that primitive hides the very thing under test.)"""

    class _FakeResponse:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def geturl(self):
            return "https://docs.google.com/spreadsheets/d/abc/pub?output=csv"

        def read(self, _n):
            return b"date,amount\n2026-06-01,10.00\n"

    monkeypatch.setattr(
        "backend.services.import_common.pinned_urlopen", lambda *a, **k: _FakeResponse()
    )
    content, hint = fetch_url_bytes("https://docs.google.com/spreadsheets/d/abc/pub?output=csv")
    assert content.startswith(b"date,amount")
    assert hint == "pub"  # last path segment, feeding format detection
