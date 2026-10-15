"""Unit tests for backend.services.favicons — the server-side favicon proxy.

Covers:
- _detect_image: recognizes png/ico/gif/jpeg/webp/bmp by magic; rejects html/svg/empty
- get_favicon: rejects malformed domains (scheme, slash, spaces, underscore, too long)
- get_favicon: resolves /favicon.ico, caches bytes + index, second call hits cache (no refetch)
- get_favicon: parses <link rel=icon> from page HTML and fetches the declared icon
- get_favicon: a miss is negative-cached (no refetch within the negative TTL)
- _safe_fetch: a redirect to a private host is refused (SSRF re-validation per hop)
- _safe_fetch: an oversized body is refused (size cap); a small body is returned
"""

from __future__ import annotations

import json

import pytest

import backend.services.favicons as fav

_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
_ICO = b"\x00\x00\x01\x00" + b"\x00" * 32


@pytest.fixture
def cache(tmp_path, monkeypatch):
    """Isolate the favicon cache (dir + index) to a temp path."""
    d = tmp_path / "favicons"
    monkeypatch.setattr(fav, "_DIR", d)
    monkeypatch.setattr(fav, "_INDEX", d / "index.json")
    return d


# ── magic-byte detection ─────────────────────────────────────────────────────


def test_detect_image_recognizes_known_formats():
    assert fav._detect_image(_PNG) == ("png", "image/png")
    assert fav._detect_image(_ICO) == ("ico", "image/x-icon")
    assert fav._detect_image(b"GIF89a" + b"\x00" * 8) == ("gif", "image/gif")
    assert fav._detect_image(b"\xff\xd8\xff" + b"\x00" * 8) == ("jpg", "image/jpeg")
    assert fav._detect_image(b"RIFF\x00\x00\x00\x00WEBP\x00\x00\x00\x00") == ("webp", "image/webp")
    assert fav._detect_image(b"BM" + b"\x00" * 8) == ("bmp", "image/bmp")


def test_detect_image_rejects_non_raster():
    # An HTML 404 page served as 200, and a scriptable SVG, must both be refused.
    assert fav._detect_image(b"<!DOCTYPE html><html>404</html>") is None
    assert (
        fav._detect_image(b'<?xml version="1.0"?><svg xmlns="http://www.w3.org/2000/svg">') is None
    )
    assert fav._detect_image(b"") is None
    assert fav._detect_image(b"ab") is None


# ── domain validation ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "   ",
        "exam ple.com",
        "http://example.com",
        "example.com/path",
        "under_score.com",
        "bang!.com",
        "a" * 254,
    ],
)
def test_get_favicon_rejects_bad_domains(bad, cache, monkeypatch):
    attempted = []
    monkeypatch.setattr(fav, "_resolve", lambda h: attempted.append(h) or None)
    assert fav.get_favicon(bad) is None
    assert attempted == []  # never even attempts a network resolve


# ── resolve + cache ────────────────────────────────────────────────────────────


def test_get_favicon_resolves_favicon_ico_and_caches(cache, monkeypatch):
    calls = []

    def fake_fetch(url, max_bytes):
        calls.append(url)
        if url == "https://acme.com/favicon.ico":
            return _PNG
        return None  # no homepage HTML

    monkeypatch.setattr(fav, "_safe_fetch", fake_fetch)

    body, ct = fav.get_favicon("acme.com")
    assert body == _PNG
    assert ct == "image/png"
    assert (cache / "acme.com.png").read_bytes() == _PNG
    idx = json.loads((cache / "index.json").read_text())
    assert idx["acme.com"]["ok"] is True

    calls.clear()
    body2, ct2 = fav.get_favicon("acme.com")
    assert (body2, ct2) == (_PNG, "image/png")
    assert calls == []  # served from cache — no second fetch


def test_get_favicon_uppercase_domain_is_normalized(cache, monkeypatch):
    monkeypatch.setattr(
        fav, "_safe_fetch", lambda url, mb: _PNG if url == "https://acme.com/favicon.ico" else None
    )
    assert fav.get_favicon("ACME.com") == (_PNG, "image/png")


def test_get_favicon_parses_declared_link_icon(cache, monkeypatch):
    html = b'<html><head><link rel="icon" href="/static/i.png"></head></html>'

    def fake_fetch(url, max_bytes):
        if url == "https://pub.com/":
            return html
        if url == "https://pub.com/static/i.png":
            return _PNG
        return None  # the /favicon.ico fallback is never needed

    monkeypatch.setattr(fav, "_safe_fetch", fake_fetch)
    assert fav.get_favicon("pub.com") == (_PNG, "image/png")


def test_get_favicon_falls_back_to_ddg_when_direct_blocked(cache, monkeypatch):
    # A Cloudflare-challenged host: homepage + /favicon.ico both blocked (None); DDG resolves it
    # server-side as the last resort, so the icon still shows without browser-side beaconing.
    ddg = "https://icons.duckduckgo.com/ip3/blocked.com.ico"

    def fake_fetch(url, max_bytes):
        return _PNG if url == ddg else None  # direct paths blocked, DDG serves the PNG

    monkeypatch.setattr(fav, "_safe_fetch", fake_fetch)
    assert fav.get_favicon("blocked.com") == (_PNG, "image/png")


def test_get_favicon_negative_caches_miss(cache, monkeypatch):
    calls = []

    def fake_fetch(url, max_bytes):
        calls.append(url)
        return None  # nothing resolves

    monkeypatch.setattr(fav, "_safe_fetch", fake_fetch)
    assert fav.get_favicon("nope.com") is None
    idx = json.loads((cache / "index.json").read_text())
    assert idx["nope.com"]["ok"] is False

    calls.clear()
    assert fav.get_favicon("nope.com") is None
    assert calls == []  # negative-cached — no refetch within TTL


# ── _safe_fetch: SSRF re-validation + size cap (fake httpx, no network) ─────────


class _FakeResp:
    def __init__(self, status, headers=None, chunks=()):
        self.status_code = status
        self.headers = headers or {}
        self.is_redirect = status in (301, 302, 303, 307, 308)
        self._chunks = chunks

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def iter_raw(self):  # _safe_fetch reads RAW bytes (no content-decoding) so a bomb can't expand
        yield from self._chunks


class _FakeClient:
    def __init__(self, responses):
        self._responses = list(responses)
        self.urls = []
        self.sent_headers = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def stream(self, method, url, headers=None):
        self.urls.append(url)
        self.sent_headers.append(headers or {})
        return self._responses.pop(0)


def _fake_guard(url):
    """Deterministic SSRF guard for tests — blocks the metadata IP / localhost without DNS."""
    from urllib.parse import urlsplit

    from backend.core.net_guard import UnsafeUrlError

    host = urlsplit(url).hostname or ""
    if host in ("169.254.169.254", "localhost", "127.0.0.1"):
        raise UnsafeUrlError("blocked")
    return url


def _patch(monkeypatch, client):
    import httpx

    monkeypatch.setattr(fav, "assert_public_url", _fake_guard)
    monkeypatch.setattr(httpx, "Client", lambda *a, **k: client)


def test_safe_fetch_refuses_redirect_to_private_host(monkeypatch):
    client = _FakeClient([_FakeResp(302, {"location": "http://169.254.169.254/"})])
    _patch(monkeypatch, client)
    assert fav._safe_fetch("https://evil.com/favicon.ico", 4096) is None


def test_safe_fetch_refuses_http_downgrade_redirect(monkeypatch):
    # A redirect from https → cleartext http is refused (no on-path tamper surface for a favicon).
    client = _FakeClient([_FakeResp(302, {"location": "http://acme.com/favicon.ico"})])
    _patch(monkeypatch, client)
    assert fav._safe_fetch("https://acme.com/", 4096) is None


def test_safe_fetch_caps_body_size(monkeypatch):
    big = [b"x" * 8 for _ in range(100)]  # 800 bytes
    client = _FakeClient([_FakeResp(200, {}, big)])
    _patch(monkeypatch, client)
    assert fav._safe_fetch("https://acme.com/favicon.ico", max_bytes=100) is None


def test_safe_fetch_refuses_declared_oversize_content_length(monkeypatch):
    # Content-Length over the cap is refused BEFORE the body is read.
    client = _FakeClient([_FakeResp(200, {"content-length": "999999"}, [_PNG])])
    _patch(monkeypatch, client)
    assert fav._safe_fetch("https://acme.com/favicon.ico", max_bytes=1024) is None


def test_safe_fetch_returns_small_body_and_requests_identity_encoding(monkeypatch):
    client = _FakeClient([_FakeResp(200, {}, [_PNG])])
    _patch(monkeypatch, client)
    assert fav._safe_fetch("https://acme.com/favicon.ico", max_bytes=10_000) == _PNG
    # Asks for an uncompressed body so the wire-byte cap is a true cap (no gzip-bomb expansion).
    assert client.sent_headers[0].get("Accept-Encoding") == "identity"
