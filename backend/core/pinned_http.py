"""DNS-rebind-safe HTTP(S) fetch (ADR ``2026-07-16-dns-rebind-pinned-fetch``).

``net_guard.assert_public_url`` validates a URL's resolved IP at ONE point in time, but a plain
``urllib.request.urlopen(url)`` (or any other client) re-resolves DNS again when it actually
connects. A low-TTL attacker-controlled domain can answer a public IP for the validation lookup
and a private one (``169.254.169.254``, ``127.0.0.1``, a LAN host) for the connect lookup —
classic DNS-rebinding TOCTOU, newly reachable unattended via the fast RSS poll loop over
user-settable feed URLs.

``pinned_urlopen`` closes this by resolving+validating ONCE via ``net_guard.resolve_public_url``
and then PINNING the actual TCP connection to that exact validated IP — the OS resolver is never
asked again for this request — while still sending the ORIGINAL hostname as the ``Host`` header
(http) / SNI + certificate-verification name (https), so the request is indistinguishable from a
normal fetch to the far end and TLS still validates the real site's certificate. Every redirect
hop repeats the full resolve-and-pin cycle from scratch (a redirect to a rebinding host is
refused on the hop, matching the pre-existing manual-redirect posture this replaces).

Mostly stdlib (``urllib``, ``http.client``, ``ssl``, ``socket``) — a ``core/`` leaf.
One intentional soft-import: ``certifi``, used only when the platform CA bundle is missing
(common on python.org macOS installs that never ran ``Install Certificates.command``).
"""

from __future__ import annotations

import logging
import os
import socket
import ssl
import urllib.error
import urllib.request
from http.client import HTTPConnection, HTTPResponse, HTTPSConnection
from pathlib import Path
from urllib.parse import urljoin, urlsplit

MAX_REDIRECTS = 5
_REDIRECT_CODES = (301, 302, 303, 307, 308)

# Headers that must NEVER be replayed onto a redirect hop landing on a DIFFERENT origin —
# mirrors the `requests` library's `rebuild_auth`, the standard reference behavior for this
# exact problem (ADR 2026-07-16-dns-rebind-pinned-fetch's MEDIUM finding #5 in the
# 2026-08-14-mcp-oauth security review): an open redirect, or a compromised CDN in front of a
# legitimate endpoint, must not be able to walk off with a live credential.
_SENSITIVE_CROSS_ORIGIN_HEADERS = frozenset({"authorization", "proxy-authorization", "cookie"})


def _origin(url: str) -> tuple[str, str, int]:
    """``(scheme, host, port)`` used to detect a cross-origin redirect hop (see
    ``pinned_urlopen``'s cross-origin header strip, above).

    SECURITY: ``urlsplit(...).port`` raises a bare ``ValueError`` on a malformed port (e.g.
    out of the 0-65535 range) instead of returning ``None`` — reachable from a redirect
    ``Location:`` header on ANY hop, which is attacker-controlled. Converted into
    ``net_guard.UnsafeUrlError`` (the same type ``resolve_public_url`` already raises for an
    unsafe hop), which ``pinned_urlopen``'s callers already handle — no new exception type,
    no new catch clause needed anywhere this is used."""
    from backend.core.net_guard import UnsafeUrlError

    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    default_port = 443 if scheme == "https" else 80
    try:
        port = parts.port
    except ValueError as exc:
        raise UnsafeUrlError("a redirect target had an invalid port") from exc
    return scheme, (parts.hostname or "").lower(), port or default_port


def _strip_headers(headers: dict, names: frozenset[str]) -> dict:
    return {k: v for k, v in headers.items() if k.lower() not in names}


class PinnedFetchError(Exception):
    """A pinned fetch could not be completed (redirect loop, missing Location, network error) —
    distinct from ``net_guard.UnsafeUrlError`` (a URL/hop the guard refuses outright)."""


def strip_auto_redirect(opener: urllib.request.OpenerDirector) -> None:
    """Purge ``HTTPRedirectHandler`` from an opener so a 3xx response raises
    ``urllib.error.HTTPError`` instead of being silently followed, in place.

    ``OpenerDirector.add_handler`` (run by ``build_opener()`` for every default handler)
    registers a handler in TWO independent places: the ``.handlers`` list (used for
    ``http_open``/``https_open`` protocol dispatch) AND the ``.handle_error`` dict, keyed
    ``{protocol: {status_code: [handler, ...]}}`` (used by ``OpenerDirector.error()``, which is
    what ``HTTPErrorProcessor.http_response`` calls for any non-2xx status). Filtering only
    ``.handlers`` — the form this function replaces — leaves ``HTTPRedirectHandler`` still
    registered under ``handle_error['http'][301..308]``/``handle_error['https'][...]``, so
    ``.open()`` keeps auto-following every redirect exactly as before: found via a REAL
    ``http.server`` 302 response, not a mock — every pre-existing caller of the old
    ``.handlers``-only filter was itself only ever tested against a monkeypatched opener, which
    is why this silently never worked. Must purge BOTH registries."""
    opener.handlers = [
        h for h in opener.handlers if not isinstance(h, urllib.request.HTTPRedirectHandler)
    ]
    for by_code in opener.handle_error.values():
        for code, handlers in list(by_code.items()):
            by_code[code] = [
                h for h in handlers if not isinstance(h, urllib.request.HTTPRedirectHandler)
            ]


def _pinned_connection_classes(
    pinned_ip: str,
) -> tuple[type[HTTPConnection], type[HTTPSConnection]]:
    """One-shot ``HTTPConnection``/``HTTPSConnection`` subclasses whose ``connect()`` dials
    ``pinned_ip`` directly instead of re-resolving ``self.host`` — ``self.host`` stays the
    ORIGINAL hostname (untouched), so the ``Host`` header, SNI, and certificate-hostname check
    all still validate against the real name, exactly like an unpinned connection would."""

    class _PinnedHTTPConnection(HTTPConnection):
        def connect(self) -> None:
            # Deliberately NOT stock HTTPConnection.connect(): dials pinned_ip unconditionally
            # instead of self.host, and does not consult self._tunnel_host — a proxy env var
            # (http_proxy/https_proxy) that would normally set up CONNECT tunneling through
            # ProxyHandler is therefore silently NOT honored by a pinned fetch (see the ADR's
            # residual-risk note: safe against a rebind-via-proxy hole, but an operator relying
            # on an egress proxy for these specific fetchers will find it bypassed).
            self.sock = socket.create_connection(
                (pinned_ip, self.port), self.timeout, source_address=self.source_address
            )
            self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

    class _PinnedHTTPSConnection(HTTPSConnection):
        def connect(self) -> None:
            # Same proxy caveat as _PinnedHTTPConnection above — self._tunnel_host is ignored.
            sock = socket.create_connection(
                (pinned_ip, self.port), self.timeout, source_address=self.source_address
            )
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            # server_hostname=self.host (never pinned_ip): the TCP peer is pinned, but SNI and
            # the certificate-hostname check still target the name the user/feed gave us.
            self.sock = self._context.wrap_socket(sock, server_hostname=self.host)

    return _PinnedHTTPConnection, _PinnedHTTPSConnection


def https_context() -> ssl.SSLContext:
    """TLS trust store for outbound HTTPS — verification always ON.

    Public: every stdlib ``urllib`` opener in the app (CoinGecko, pinned fetches) builds its
    ``HTTPSHandler`` from this, so the certifi fallback below is one decision, not one per
    call site.

    Prefer an operator-supplied ``SSL_CERT_FILE`` / ``SSL_CERT_DIR``. Otherwise use the
    platform OpenSSL bundle when it exists on disk. When that file is missing (python.org
    macOS without ``Install Certificates.command``), soft-import ``certifi`` — the same
    bundle ``httpx`` already uses — so RSS and other pinned fetches do not die with
    ``CERTIFICATE_VERIFY_FAILED``. Never disables hostname or certificate checks.
    """
    if os.environ.get("SSL_CERT_FILE") or os.environ.get("SSL_CERT_DIR"):
        return ssl.create_default_context()
    paths = ssl.get_default_verify_paths()
    cafile = paths.cafile or paths.openssl_cafile
    if cafile and Path(cafile).is_file():
        return ssl.create_default_context()
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


_log = logging.getLogger(__name__)

_TRUST_STORE_DECISION: str | None = None


def _is_file(path: str | None) -> bool:
    return bool(path) and Path(path).is_file()


def _is_dir(path: str | None) -> bool:
    return bool(path) and Path(path).is_dir()


def trust_store_decision() -> str | None:
    """What ``ensure_default_trust_store`` concluded — ``None`` until it has run."""
    return _TRUST_STORE_DECISION


def ensure_default_trust_store() -> str | None:
    """Give EVERY stdlib TLS caller in this process a populated trust store — run once at boot.

    A python.org macOS Python whose ``Install Certificates.command`` was never run has NO CA
    file at OpenSSL's compiled-in path, so each bare ``urllib.request.urlopen`` builds an
    EMPTY store and fails ``CERTIFICATE_VERIFY_FAILED`` — while curl_cffi/httpx (certifi)
    keep working, so prices and charts looked fine and only the palette's market search,
    Telegram, webhooks and MCP died, silently (2026-09-21). OpenSSL consults ``SSL_CERT_FILE``
    every time a default context loads its certs, so pointing it at a real bundle here fixes
    all of them at once, without loosening verification anywhere.

    Precedence: an operator ``SSL_CERT_FILE`` that EXISTS (or an ``SSL_CERT_DIR`` that is a
    directory) wins untouched. A DANGLING ``SSL_CERT_FILE`` is repaired, not respected — OpenSSL
    honours it over the compiled-in path, so it yields exactly the empty store this guards
    against. Then the platform bundle if it exists, else certifi. Returns the bundle path when
    this call SET ``SSL_CERT_FILE``, else ``None``.
    """
    global _TRUST_STORE_DECISION
    env_file = os.environ.get("SSL_CERT_FILE")
    if _is_file(env_file) or (not env_file and _is_dir(os.environ.get("SSL_CERT_DIR"))):
        _TRUST_STORE_DECISION = "operator"
        return None
    paths = ssl.get_default_verify_paths()
    platform_cafile = paths.cafile or paths.openssl_cafile
    if _is_file(platform_cafile):
        _TRUST_STORE_DECISION = "platform"
        if not env_file:
            return None
        _log.warning(
            "tls: SSL_CERT_FILE=%s does not exist — using the platform bundle %s instead",
            env_file,
            platform_cafile,
        )
        os.environ["SSL_CERT_FILE"] = str(platform_cafile)
        return str(platform_cafile)
    try:
        import certifi
    except ImportError:
        _TRUST_STORE_DECISION = "unavailable"
        _log.warning(
            "tls: no CA bundle at %s and certifi is not installed — every stdlib HTTPS call "
            "will fail certificate verification",
            platform_cafile,
        )
        return None
    bundle = certifi.where()
    os.environ["SSL_CERT_FILE"] = bundle
    _TRUST_STORE_DECISION = "certifi"
    _log.warning(
        "tls: no CA bundle at %s — using certifi's (%s) for this process. On a python.org "
        "macOS build, run 'Install Certificates.command' to fix the interpreter itself.",
        platform_cafile,
        bundle,
    )
    return bundle


def _build_pinned_opener(pinned_ip: str) -> urllib.request.OpenerDirector:
    """A one-shot opener bound to ``pinned_ip`` with auto-redirect-following DISABLED — a 3xx
    response raises ``urllib.error.HTTPError`` instead of silently being followed, so
    ``pinned_urlopen`` can re-resolve-and-pin each hop itself rather than the OS resolving the
    hop's host a second, unchecked time."""
    http_cls, https_cls = _pinned_connection_classes(pinned_ip)

    class _PinnedHTTPHandler(urllib.request.HTTPHandler):
        def http_open(self, req):
            return self.do_open(http_cls, req)

    class _PinnedHTTPSHandler(urllib.request.HTTPSHandler):
        def https_open(self, req):
            # Pass self._context (set by HTTPSHandler.__init__) so TLS uses https_context(),
            # not a fresh default that ignores the certifi fallback.
            return self.do_open(https_cls, req, context=self._context)

    opener = urllib.request.build_opener(
        _PinnedHTTPHandler,
        _PinnedHTTPSHandler(context=https_context()),
    )
    strip_auto_redirect(opener)
    return opener


def pinned_opener(pinned_ip: str) -> urllib.request.OpenerDirector:
    """Public handle on the one-shot pinned, no-redirect opener for callers that run their own
    request loop (the MCP HTTP client) after validating the URL themselves."""
    return _build_pinned_opener(pinned_ip)


def pinned_urlopen(
    url: str,
    *,
    headers: dict | None = None,
    data: bytes | None = None,
    method: str | None = None,
    timeout: float = 10,
    max_redirects: int = MAX_REDIRECTS,
    owner_configured: bool = False,
) -> HTTPResponse | urllib.error.HTTPError:
    """SSRF-guarded + DNS-rebind-safe fetch. Resolves, validates, and PINS every hop; returns
    the final ``http.client.HTTPResponse`` — use it exactly like a normal ``urlopen()`` result
    (``.read()``, ``.headers``, context-manager close).

    GET is the default and is UNCHANGED from before ``data=``/``method=`` existed: omit both
    and behavior is byte-identical. Pass ``data=`` (raw request body bytes) for a POST — DCR
    registration and OAuth token-exchange calls are POSTs to a URL that can come from
    server-controlled metadata, so this primitive (not a raw ``urlopen``) is what those calls
    must go through. ``method=`` overrides the inferred verb (``"POST"`` when ``data`` is
    given, else ``"GET"``) for a caller that needs an explicit method with no body (rare).

    **A POST is never silently replayed onto a redirect target.** Every hop — POST included —
    re-resolves and re-pins exactly like the GET path already did (a redirect to a rebinding
    host is refused on the hop, same as always). On top of that, the METHOD/BODY carried into
    the next hop follows real HTTP redirect semantics instead of blindly repeating the
    original request:
      * ``303`` always downgrades to a bodyless ``GET`` (RFC 7231 §6.4.4) — the standard "see
        this other resource instead" redirect;
      * ``307``/``308`` preserve the method AND body verbatim (RFC 7231 §6.4.7/.8) — the only
        codes a server may use to say "resubmit exactly this to a new URL";
      * ``301``/``302`` in response to a POST are spec-AMBIGUOUS for what a client should do
        next (RFC 7231 §6.4.2/.3 explicitly allows either), and every mainstream HTTP client
        downgrades to a bodyless GET rather than re-POSTing — this function does the same, so
        a token/registration secret in the body is never blindly forwarded to a hop this
        function has not itself decided to resubmit to.
    A GET redirect is unaffected by any of this (GET has no body to drop or preserve).

    **A sensitive header never survives a CROSS-ORIGIN redirect hop** (the mcp-oauth security
    review's MEDIUM finding #5): an ``Authorization``/``Proxy-Authorization``/``Cookie`` header
    is stripped the instant a hop's ``(scheme, host, port)`` differs from the previous hop's —
    mirroring the ``requests`` library's ``rebuild_auth``, the standard reference behavior for
    this exact problem. A hop that stays on the SAME origin keeps the header (needed for a
    same-origin path redirect during a real OAuth exchange). ``Content-Type`` is additionally
    dropped whenever a POST downgrades to a bodyless GET (303, or 301/302 on a POST) — there is
    no longer a body for it to describe.

    ``owner_configured=True`` is for a URL the owner typed into Settings (a webhook, a model
    endpoint): loopback/LAN targets pass on a self-hosted install (``net_guard.resolve_owner_url``),
    never in gateway mode. Model- and server-chosen URLs must leave it False.

    Raises ``net_guard.UnsafeUrlError`` (the URL or a redirect hop is blocked) or
    ``PinnedFetchError`` (too many redirects) — never silently falls back to an unpinned fetch.
    Any non-redirect HTTP error status (4xx/5xx) propagates as ``urllib.error.HTTPError``, same
    as a plain ``urlopen()`` call. A redirect status with NO ``Location`` header is treated as
    the FINAL response (pre-existing ``plain_get_fetch`` semantics — nothing to follow, so the
    body it shipped with the 3xx is the content) — ``urllib.error.HTTPError`` is itself a
    file-like object wrapping that body, so it's returned as-is rather than raised."""
    from backend.core.net_guard import resolve_owner_url, resolve_public_url

    resolve = resolve_owner_url if owner_configured else resolve_public_url
    next_url = url
    next_data = data
    next_method = method or ("POST" if data is not None else "GET")
    next_headers = dict(headers or {})
    for _ in range(max_redirects + 1):
        clean, pinned_ip = resolve(next_url)
        opener = _build_pinned_opener(pinned_ip)
        req = urllib.request.Request(
            clean, data=next_data, headers=next_headers, method=next_method
        )
        try:
            return opener.open(req, timeout=timeout)  # noqa: S310 (pinned + SSRF-validated)
        except urllib.error.HTTPError as exc:
            if exc.code not in _REDIRECT_CODES:
                raise
            location = exc.headers.get("Location") if exc.headers else None
            if not location:
                return exc
            hop_url = urljoin(clean, location)
            if _origin(hop_url) != _origin(clean):
                next_headers = _strip_headers(next_headers, _SENSITIVE_CROSS_ORIGIN_HEADERS)
            next_url = hop_url
            if exc.code == 303 or (exc.code in (301, 302) and next_method == "POST"):
                next_method = "GET"
                next_data = None
                next_headers = _strip_headers(next_headers, frozenset({"content-type"}))
            # 307/308 fall through unchanged: same method, same body, re-validated above.
    raise PinnedFetchError("too many redirects")
