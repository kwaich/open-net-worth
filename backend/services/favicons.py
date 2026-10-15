"""Favicon proxy — fetch a publisher's site icon SERVER-SIDE and cache it.

Why a server proxy (ADR 0025): the prior client-side ``https://{domain}/favicon.ico`` direct
fetch missed any site whose icon isn't at that exact path, and pointing the BROWSER at a
third-party resolver (Google s2 / DuckDuckGo) would beacon every viewed publisher to that third
party. The server already fetches these domains for RSS/briefs, so resolving their favicon here
adds no NEW browser-side exposure, fixes reliability (it parses the page's declared
``<link rel=icon>``), and keeps the browser talking only to this instance. As a LAST resort, a
site that bot-blocks a direct server fetch (Cloudflare challenge) is resolved via DuckDuckGo's
icon service SERVER-side (``_resolve``) — the browser still beacons no one; see ADR 0025.

Security:
  * SSRF — the domain is UNTRUSTED (feed/LLM-derived). Every fetch is validated by
    ``core.net_guard.assert_public_url`` and follows redirects MANUALLY, re-validating each hop
    — a public host that 30x-redirects to ``169.254.169.254`` / localhost / a private LAN host
    is refused on the hop.
  * Body validated by MAGIC BYTES, not the declared content-type — this also rejects an HTML
    404 page served as 200 and a scriptable ``image/svg+xml`` (no raster magic → refused).
  * Bodies are read RAW (no content-decoding) and capped on the WIRE, so a gzip-bomb can't
    expand past the cap in memory; ``https`` only (no cleartext downgrade on a redirect/link).
  * The endpoint is auth-gated (the ``<img>`` carries the session cookie) AND rate-limited, so
    this is NOT an open proxy / outbound-amplifier.
  * Cache lives under ``CACHE_DIR`` (checkpoint-excluded); domain keys are validated to a clean
    host label so they are safe as filenames (no path traversal).

Known limitation (ADR ``2026-07-16-dns-rebind-pinned-fetch``): ``assert_public_url`` resolves DNS
at validation time, httpx resolves again at connect time — a DNS-rebinding attacker could answer
differently. ``monitoring.collect``/``ai.search.base.plain_get_fetch`` closed this via
``core.pinned_http.pinned_urlopen`` (IP-pinned ``http.client`` connections); this module is NOT
yet migrated (httpx has no equivalent request-level IP-pin hook without a custom transport) and
is mitigated in depth by the rate limit + magic-byte validation + auth gate in the meantime.
"""

from __future__ import annotations

import json
import logging
import re
import time
from html.parser import HTMLParser
from urllib.parse import urljoin

from backend.core.io import atomic_write_bytes, atomic_write_text
from backend.core.net_guard import UnsafeUrlError, assert_public_url
from backend.core.paths import FAVICONS_DIR

_log = logging.getLogger(__name__)

_DIR = FAVICONS_DIR
_INDEX = _DIR / "index.json"
_UA = "OpenNetWorth/1.0 (favicon-fetch)"
_TIMEOUT = 6.0
_MAX_REDIRECTS = 3
_MAX_ICON_BYTES = 256 * 1024  # a favicon is tiny; cap abuse
_MAX_HTML_BYTES = 512 * 1024  # homepage HTML scanned for <link rel=icon>
_MAX_CANDIDATES = 4  # parsed <link rel=icon> candidates to try (bounds outbound fan-out)
_POS_TTL = 30 * 24 * 3600  # 30 days
# Keep the negative TTL short: a miss is often transient (a network blip, a temporarily-down
# origin) or curable by a later capability gain (e.g. the DDG fallback), and we don't want a
# site stuck on the blue fallback for a full day. Genuine icon-less sites are rare (DDG resolves
# most), so re-checking every few hours costs little.
_NEG_TTL = 6 * 3600  # 6 hours
_DOMAIN_RE = re.compile(r"^[a-z0-9.-]{1,253}$")


def _detect_image(body: bytes) -> tuple[str, str] | None:
    """Identify a raster image from its MAGIC BYTES → (ext, content_type), else None.

    Deliberately excludes SVG (no binary magic; an ``<svg>`` is scriptable) and any text/HTML
    body — so a 404 page served with status 200 is rejected, not cached as an 'icon'."""
    if len(body) < 4:
        return None
    if body.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png", "image/png"
    if body[:4] == b"\x00\x00\x01\x00":
        return "ico", "image/x-icon"
    if body[:6] in (b"GIF87a", b"GIF89a"):
        return "gif", "image/gif"
    if body[:3] == b"\xff\xd8\xff":
        return "jpg", "image/jpeg"
    if body[:4] == b"RIFF" and body[8:12] == b"WEBP":
        return "webp", "image/webp"
    if body[:2] == b"BM":
        return "bmp", "image/bmp"
    return None


def _safe_https(url: str) -> str | None:
    """``assert_public_url`` + require ``https``. Returns the safe URL, or None when blocked
    (SSRF) or non-https (no cleartext downgrade for a favicon)."""
    try:
        safe = assert_public_url(url)
    except UnsafeUrlError:
        return None
    return safe if safe.lower().startswith("https://") else None


def _safe_fetch(url: str, max_bytes: int) -> bytes | None:
    """SSRF-guarded HTTPS GET → body bytes (≤ ``max_bytes``) or None.

    Redirects are followed manually (``follow_redirects=False``); EVERY hop's ``Location`` is
    re-validated AND required to stay ``https`` (``_safe_https``) before the next request. The
    body is read RAW (``iter_raw`` — NO content-decoding) and capped on the WIRE, so a gzip-bomb
    can't expand past the cap in memory (an undecoded gzip body just fails the magic-byte check
    downstream); ``Accept-Encoding: identity`` keeps an honest server's bytes usable. A declared
    over-cap ``Content-Length`` is refused before any body is read."""
    import httpx

    safe = _safe_https(url)
    if safe is None:
        return None
    headers = {"User-Agent": _UA, "Accept-Encoding": "identity"}
    try:
        with httpx.Client(timeout=_TIMEOUT, follow_redirects=False) as client:
            for _ in range(_MAX_REDIRECTS + 1):
                with client.stream("GET", safe, headers=headers) as resp:
                    if resp.is_redirect:
                        loc = resp.headers.get("location")
                        if not loc:
                            return None
                        safe = _safe_https(urljoin(safe, loc))
                        if safe is None:
                            return None
                        continue
                    if resp.status_code != 200:
                        return None
                    clen = resp.headers.get("content-length")
                    if clen and clen.isdigit() and int(clen) > max_bytes:
                        return None  # declared over-cap — refuse before reading
                    chunks: list[bytes] = []
                    total = 0
                    for chunk in resp.iter_raw():
                        total += len(chunk)
                        if total > max_bytes:
                            return None  # oversized on the wire — refuse
                        chunks.append(chunk)
                    return b"".join(chunks)
            return None  # too many redirects
    except Exception as exc:  # network error, bad TLS, etc. — best-effort
        _log.debug("favicon fetch failed for %s: %s", url, type(exc).__name__)
        return None


class _IconLinkParser(HTMLParser):
    """Collect href of every ``<link rel="...icon...">`` (icon / shortcut icon / apple-touch)."""

    def __init__(self) -> None:
        super().__init__()
        self.hrefs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "link":
            return
        a = {k.lower(): (v or "") for k, v in attrs}
        if "icon" in a.get("rel", "").lower() and a.get("href"):
            self.hrefs.append(a["href"])


def _parse_icon_links(html: str, base_url: str) -> list[str]:
    """Absolute http(s) icon URLs declared in the page HTML (capped at ``_MAX_CANDIDATES``)."""
    parser = _IconLinkParser()
    try:
        parser.feed(html)
    except Exception:
        return []
    out: list[str] = []
    for href in parser.hrefs:
        absu = urljoin(base_url, href.strip())
        if absu.startswith("https://"):  # https only — no cleartext favicon fetch
            out.append(absu)
    return out[:_MAX_CANDIDATES]


# Privacy-preserving LAST-RESORT resolver for hosts that bot-block a direct server fetch (e.g.
# Cloudflare "managed challenge" 403s every programmatic request — even with a browser UA). DDG's
# icon service is allowlisted by those CDNs and returns a PNG. This runs SERVER-side only and only
# after direct resolution fails, so the user's browser never beacons a third party, and a
# well-behaved site is resolved directly without DDG ever being touched. (`host` is regex-validated
# to a clean label, so it is safe to interpolate into the path — no URL injection.)
_DDG_ICON = "https://icons.duckduckgo.com/ip3/{host}.ico"


def _resolve(host: str) -> tuple[bytes, str, str] | None:
    """Resolve a host to icon bytes: the page's declared icon → /favicon.ico → DDG (last resort)."""
    candidates: list[str] = []
    html = _safe_fetch(f"https://{host}/", _MAX_HTML_BYTES)
    if html:
        candidates.extend(_parse_icon_links(html.decode("utf-8", "replace"), f"https://{host}/"))
    candidates.append(f"https://{host}/favicon.ico")
    candidates.append(_DDG_ICON.format(host=host))

    seen: set[str] = set()
    for url in candidates:
        if url in seen:
            continue
        seen.add(url)
        body = _safe_fetch(url, _MAX_ICON_BYTES)
        if not body:
            continue
        detected = _detect_image(body)
        if detected:
            ext, content_type = detected
            return body, ext, content_type
    return None


def _load_index() -> dict:
    try:
        if _INDEX.exists():
            return json.loads(_INDEX.read_text("utf-8"))
    except Exception:
        _log.debug("favicon index read failed", exc_info=True)
    return {}


def _save_index(idx: dict) -> None:
    try:
        atomic_write_text(_INDEX, json.dumps(idx))
    except Exception:
        _log.debug("favicon index write failed", exc_info=True)


def _read_cached(host: str, ent: dict) -> tuple[bytes, str] | None:
    ext, content_type = ent.get("ext"), ent.get("content_type")
    if not ext or not content_type:
        return None
    path = _DIR / f"{host}.{ext}"
    try:
        if path.exists():
            return path.read_bytes(), content_type
    except Exception:
        _log.debug("favicon bytes read failed for %s", host, exc_info=True)
    return None


def _store(host: str, icon: tuple[bytes, str, str] | None, idx: dict, now: float) -> None:
    if icon is not None:
        body, ext, content_type = icon
        try:
            atomic_write_bytes(_DIR / f"{host}.{ext}", body)
        except Exception:
            _log.debug("favicon bytes write failed for %s", host, exc_info=True)
            return
        idx[host] = {"ok": True, "ext": ext, "content_type": content_type, "ts": now}
    else:
        idx[host] = {"ok": False, "ts": now}  # negative-cache the miss
    _save_index(idx)


def get_favicon(domain: str) -> tuple[bytes, str] | None:
    """Return ``(image_bytes, content_type)`` for ``domain``'s favicon, or None.

    Serves from the ``CACHE_DIR`` cache (positive TTL 30d, negative TTL 1d) before fetching.
    The domain must be a clean host label (``a-z0-9.-``); anything else is refused outright."""
    host = (domain or "").strip().lower().rstrip(".")
    if not host or not _DOMAIN_RE.match(host):
        return None

    now = time.time()
    idx = _load_index()
    ent = idx.get(host)
    if ent:
        age = now - float(ent.get("ts", 0) or 0)
        if ent.get("ok") and age < _POS_TTL:
            cached = _read_cached(host, ent)
            if cached:
                return cached
        elif not ent.get("ok") and age < _NEG_TTL:
            return None  # negative-cached miss — don't hammer the origin

    icon = _resolve(host)
    _store(host, icon, idx, now)
    return (icon[0], icon[2]) if icon is not None else None
