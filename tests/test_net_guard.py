"""SSRF guard (core.net_guard.assert_public_url) + the fetch_page tool's use of it.

The guard is the load-bearing control that stops web_search/fetch_page being turned into an
SSRF: it rejects non-http(s) schemes, missing hosts, and any host that resolves to a
loopback/private/link-local/reserved/multicast address."""

from __future__ import annotations

import asyncio
import json

import pytest

from backend.core.net_guard import UnsafeUrlError, assert_public_url, resolve_public_url


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost/x",
        "http://localhost:8080/api/secrets",
        "http://sub.localhost/x",
        "http://127.0.0.1/x",
        "https://127.0.0.1:8080/",
        "http://[::1]/x",
        "http://10.0.0.5/internal",
        "http://192.168.1.1/router",
        "http://172.16.0.9/x",
        "http://169.254.169.254/latest/meta-data/",  # cloud metadata
        "http://0.0.0.0/",
        "file:///etc/passwd",
        "ftp://example.com/x",
        "gopher://example.com/x",
        "http://",  # no host
        "",  # empty
    ],
)
def test_blocks_unsafe_urls(url):
    with pytest.raises(UnsafeUrlError):
        assert_public_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/page",
        "http://example.com/",
        "https://api.tavily.com/search?q=x",
        "https://1.1.1.1/",  # a public IP literal
    ],
)
def test_allows_public_urls(url):
    assert assert_public_url(url) == url.strip()


def test_blocks_hostname_resolving_to_private(monkeypatch):
    """A hostname that DNS-resolves to a private IP is refused (not just literal 127.0.0.1)."""

    def fake_getaddrinfo(host, port, *a, **k):
        return [(2, 1, 6, "", ("10.1.2.3", 0))]

    monkeypatch.setattr("backend.core.net_guard.socket.getaddrinfo", fake_getaddrinfo)
    with pytest.raises(UnsafeUrlError):
        assert_public_url("https://evil.example.com/")


def test_allows_hostname_resolving_to_public(monkeypatch):
    def fake_getaddrinfo(host, port, *a, **k):
        return [(2, 1, 6, "", ("93.184.216.34", 0))]

    monkeypatch.setattr("backend.core.net_guard.socket.getaddrinfo", fake_getaddrinfo)
    assert assert_public_url("https://example.com/") == "https://example.com/"


def test_unresolvable_host_is_refused(monkeypatch):
    import socket as _socket

    def boom(host, port, *a, **k):
        raise _socket.gaierror("nope")

    monkeypatch.setattr("backend.core.net_guard.socket.getaddrinfo", boom)
    with pytest.raises(UnsafeUrlError):
        assert_public_url("https://does-not-exist.example/")


# ── resolve_public_url: the pin a caller connects to, not just the validated string ─


def test_resolve_public_url_returns_the_ip_for_a_literal():
    clean, ip = resolve_public_url("https://1.1.1.1/")
    assert clean == "https://1.1.1.1/"
    assert ip == "1.1.1.1"


def test_resolve_public_url_pins_the_first_validated_address(monkeypatch):
    def fake_getaddrinfo(host, port, *a, **k):
        return [
            (2, 1, 6, "", ("93.184.216.34", 0)),
            (2, 1, 6, "", ("93.184.216.35", 0)),
        ]

    monkeypatch.setattr("backend.core.net_guard.socket.getaddrinfo", fake_getaddrinfo)
    clean, ip = resolve_public_url("https://example.com/")
    assert clean == "https://example.com/"
    assert ip == "93.184.216.34"  # first address from the SAME getaddrinfo() call


def test_resolve_public_url_blocks_if_any_resolved_address_is_private(monkeypatch):
    def fake_getaddrinfo(host, port, *a, **k):
        return [(2, 1, 6, "", ("93.184.216.34", 0)), (2, 1, 6, "", ("10.0.0.5", 0))]

    monkeypatch.setattr("backend.core.net_guard.socket.getaddrinfo", fake_getaddrinfo)
    with pytest.raises(UnsafeUrlError):
        resolve_public_url("https://multi-homed.example.com/")


def test_resolve_public_url_refuses_a_malformed_port_instead_of_raising_value_error():
    """SECURITY (re-verification finding, LOW): ``urlsplit(...).port`` raises a bare
    ``ValueError`` on a malformed port (out of the 0-65535 range) instead of returning ``None``
    — this used to escape uncaught here, breaking every caller's fail-closed contract
    (``UnsafeUrlError`` only). A hostname with a bad port must be refused the same way any
    other unsafe URL is — never a raw crash."""
    with pytest.raises(UnsafeUrlError):
        resolve_public_url("https://example.com:99999/")


def test_assert_public_url_is_a_thin_wrapper_over_resolve(monkeypatch):
    """assert_public_url must stay byte-identical for existing callers that only want the
    validated string (not a pinned connection)."""

    def fake_getaddrinfo(host, port, *a, **k):
        return [(2, 1, 6, "", ("93.184.216.34", 0))]

    monkeypatch.setattr("backend.core.net_guard.socket.getaddrinfo", fake_getaddrinfo)
    assert assert_public_url("https://example.com/") == "https://example.com/"


# ── userinfo / IPv6-literal tricks — a naive host parser could be fooled, urlsplit isn't ──


@pytest.mark.parametrize(
    "url",
    [
        "http://trusted-looking-name.com@169.254.169.254/steal",  # userinfo hides the real host
        "http://trusted-looking-name.com@127.0.0.1/steal",
        "http://[::1]/x",  # IPv6 loopback literal
        "http://[::ffff:127.0.0.1]/x",  # IPv4-mapped IPv6 loopback literal
        "http://[fe80::1]/x",  # IPv6 link-local literal
    ],
)
def test_blocks_userinfo_and_ipv6_tricks(url):
    with pytest.raises(UnsafeUrlError):
        resolve_public_url(url)


def test_userinfo_prefix_that_looks_like_an_internal_ip_is_still_fine_when_host_is_public():
    """The mirror case: an IP-looking USERINFO in front of a genuinely public host must NOT be
    treated as the host — proves the guard reads the real (post-`@`) hostname, not a substring
    match anywhere in the URL."""
    clean, ip = resolve_public_url("http://169.254.169.254@1.1.1.1/")
    assert ip == "1.1.1.1"


# ── the fetch_page tool refuses an unsafe URL before any provider call ────────────


def test_fetch_page_tool_blocks_localhost(monkeypatch):
    from backend.ai.tools import misc

    def _explode():  # the provider must NOT be reached for a blocked URL
        raise AssertionError("get_search_provider should not be called for a blocked URL")

    monkeypatch.setattr(misc, "get_search_provider", _explode)
    text, proposed = asyncio.run(misc.fetch_page({"url": "http://127.0.0.1:8080/api"}))
    assert proposed is None
    payload = json.loads(text)
    assert "fetch blocked" in payload["content"]


# ── plain_get_fetch: DNS-rebind-safe + redirect-hop-safe (ADR 2026-07-16) ─────────
#
# plain_get_fetch now runs through core.pinned_http.pinned_urlopen (see tests/test_pinned_http.py
# for the deep, network-free coverage of the resolve+pin+redirect mechanism itself). These tests
# prove the WIRING: plain_get_fetch surfaces UnsafeUrlError / PinnedFetchError as the documented
# "[fetch blocked: ...]" / "[fetch failed: ...]" strings, and a normal fetch decodes the body.


class _FakeHttpResponse:
    """Minimal stand-in for the http.client.HTTPResponse pinned_urlopen returns."""

    def __init__(self, body: bytes, charset: str | None = "utf-8"):
        self._body = body
        self.headers = _FakeHeaders(charset)

    def read(self, n=None):
        return self._body if n is None else self._body[:n]

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _FakeHeaders:
    def __init__(self, charset):
        self._charset = charset

    def get_content_charset(self):
        return self._charset


def test_plain_get_fetch_blocks_an_unsafe_url(monkeypatch):
    from backend.ai.search import base
    from backend.core.net_guard import UnsafeUrlError

    def fake_pinned_urlopen(url, **kw):
        raise UnsafeUrlError("Refusing to fetch a private or internal address.")

    monkeypatch.setattr("backend.core.pinned_http.pinned_urlopen", fake_pinned_urlopen)

    out = asyncio.run(base.plain_get_fetch("http://169.254.169.254/latest/meta-data/"))
    assert "fetch blocked" in out


def test_plain_get_fetch_returns_decoded_body(monkeypatch):
    from backend.ai.search import base

    captured = {}

    def fake_pinned_urlopen(url, *, headers=None, timeout=None, max_redirects=None):
        captured["url"] = url
        captured["max_redirects"] = max_redirects
        return _FakeHttpResponse(b"final page body")

    monkeypatch.setattr("backend.core.pinned_http.pinned_urlopen", fake_pinned_urlopen)

    out = asyncio.run(base.plain_get_fetch("https://example.com/start"))
    assert out == "final page body"
    assert captured["url"] == "https://example.com/start"
    assert captured["max_redirects"] == base._MAX_FETCH_REDIRECTS


def test_plain_get_fetch_reports_too_many_redirects(monkeypatch):
    from backend.ai.search import base
    from backend.core.pinned_http import PinnedFetchError

    def fake_pinned_urlopen(url, **kw):
        raise PinnedFetchError("too many redirects")

    monkeypatch.setattr("backend.core.pinned_http.pinned_urlopen", fake_pinned_urlopen)

    out = asyncio.run(base.plain_get_fetch("https://example.com/start"))
    assert "too many redirects" in out


def test_plain_get_fetch_no_content_extracted(monkeypatch):
    from backend.ai.search import base

    monkeypatch.setattr(
        "backend.core.pinned_http.pinned_urlopen",
        lambda url, **kw: _FakeHttpResponse(b"   "),
    )
    out = asyncio.run(base.plain_get_fetch("https://example.com/blank"))
    assert out == "[no content extracted]"


def test_plain_get_fetch_caps_an_oversized_body(monkeypatch):
    """A hostile/huge page must not be read unbounded — resp.read(_MAX_FETCH_BYTES) truncates
    on the wire instead of buffering the whole thing (mirrors collect.py's _RSS_MAX_BYTES cap)."""
    from backend.ai.search import base

    oversized = b"x" * (base._MAX_FETCH_BYTES + 1000)

    monkeypatch.setattr(
        "backend.core.pinned_http.pinned_urlopen",
        lambda url, **kw: _FakeHttpResponse(oversized),
    )
    out = asyncio.run(base.plain_get_fetch("https://example.com/huge"))
    assert len(out) == base._MAX_FETCH_BYTES  # truncated, not crashed


# ── plain_get_fetch: local trafilatura extraction (MVP readability pass) ─────────

_NOISY_ARTICLE_HTML = b"""
<html><head><title>Example</title></head>
<body>
<nav><a href="/">Home</a><a href="/about">About</a><a href="/contact">Contact</a></nav>
<header><div class="ad-banner">ADVERTISEMENT: Buy now! Limited offer!</div></header>
<article>
<h1>A Real Article Headline</h1>
<p>This is the first real paragraph of substantive article content that a reader actually
came here for, explaining the topic in enough detail to be meaningfully long.</p>
<p>This is a second paragraph continuing the explanation with more real information, so the
extracted body is clearly the article and not scaffolding around it.</p>
</article>
<footer>Copyright 2026 Example Corp. Privacy Policy. Terms of Service. Cookie settings.</footer>
<div class="sidebar"><a href="/related-1">Related post 1</a><a href="/related-2">Related post 2</a></div>
</body></html>
"""


def test_plain_get_fetch_extracts_article_and_drops_boilerplate(monkeypatch):
    """The MVP readability pass: trafilatura strips nav/ad/footer/sidebar noise and returns
    just the article body — the fetch_page caller no longer sees raw chrome around it."""
    from backend.ai.search import base

    monkeypatch.setattr(
        "backend.core.pinned_http.pinned_urlopen",
        lambda url, **kw: _FakeHttpResponse(_NOISY_ARTICLE_HTML),
    )
    out = asyncio.run(base.plain_get_fetch("https://example.com/article"))
    assert "A Real Article Headline" in out
    assert "first real paragraph of substantive article content" in out
    assert "ADVERTISEMENT" not in out
    assert "Privacy Policy" not in out
    assert "Related post" not in out


def test_plain_get_fetch_falls_back_to_raw_when_extraction_finds_too_little(monkeypatch):
    """A page trafilatura can't extract anything meaningful from (e.g. a JS-only shell with
    no server-rendered body) must fall back to the raw text — never worse than before the
    extraction pass was added, never a bare '[no content extracted]' when there WAS a body."""
    from backend.ai.search import base

    js_shell = b'<html><body><div id="root"></div><script>/* app bundle */</script></body></html>'
    monkeypatch.setattr(
        "backend.core.pinned_http.pinned_urlopen",
        lambda url, **kw: _FakeHttpResponse(js_shell),
    )
    out = asyncio.run(base.plain_get_fetch("https://example.com/spa"))
    assert out == js_shell.decode("utf-8")
