from __future__ import annotations

import html
import re
from urllib.parse import parse_qs, urlsplit

import httpx

from backend.ai.search.base import SearchProvider, plain_get_fetch

# DuckDuckGo — the KEY-FREE provider (the zero-config default). DuckDuckGo has no official
# search API, but its "HTML" endpoint (the no-JS lite frontend) returns a static results page
# we can parse: each hit is an <a class="result__a" href="…">title</a> followed by an
# <a class="result__snippet">snippet</a>. The href is a DuckDuckGo redirect
# (/l/?uddg=<url-encoded target>) — we decode the real target out of the `uddg` query param.
# No auth, no key. Best-effort: a layout change degrades to fewer/zero rows, never a crash.
_HTML = "https://html.duckduckgo.com/html/"

# <a ... class="result__a" ... href="HREF">INNER</a>  (attribute order varies, so match loosely)
_RESULT_A = re.compile(
    r'<a[^>]*class="[^"]*result__a[^"]*"[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
    re.IGNORECASE | re.DOTALL,
)
# the snippet anchor/span that follows a result
_SNIPPET = re.compile(
    r'class="[^"]*result__snippet[^"]*"[^>]*>(.*?)</a>',
    re.IGNORECASE | re.DOTALL,
)
_TAGS = re.compile(r"<[^>]+>")


def _strip(fragment: str) -> str:
    """Drop HTML tags + unescape entities → plain text."""
    return html.unescape(_TAGS.sub("", fragment or "")).strip()


def _real_url(href: str) -> str:
    """Decode DuckDuckGo's /l/?uddg=<target> redirect to the real target URL.

    DuckDuckGo wraps every result href in a redirect; the actual destination is the `uddg`
    query parameter (URL-encoded). A protocol-relative `//duckduckgo.com/l/?...` is handled.
    A non-redirect href (rare) is returned as-is."""
    raw = html.unescape(href or "")
    if raw.startswith("//"):
        raw = "https:" + raw
    parts = urlsplit(raw)
    if parts.path.endswith("/l/"):
        target = parse_qs(parts.query).get("uddg", [])
        if target:
            return target[0]
    return raw


class DuckDuckGoProvider(SearchProvider):
    """Key-free web search via DuckDuckGo's HTML endpoint. The zero-config default provider."""

    plain_page_fetch = True  # fetch_page is the plain GET: og:image rides along

    def __init__(self, api_key: str = "") -> None:
        # No key required — the parameter exists only so the factory signature is uniform.
        self.api_key = ""

    async def search(self, query: str, max_results: int = 8) -> list[dict]:
        params = {"q": query}
        async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
            resp = await client.post(
                _HTML,
                data=params,
                headers={
                    "User-Agent": "Mozilla/5.0 (compatible; OpenNetWorth/1.0)",
                    "Accept": "text/html",
                },
            )
            resp.raise_for_status()
            body = resp.text or ""

        out: list[dict] = []
        snippets = _SNIPPET.findall(body)
        for i, m in enumerate(_RESULT_A.finditer(body)):
            if len(out) >= max_results:
                break
            url = _real_url(m.group(1))
            title = _strip(m.group(2))
            if not url or not title:
                continue
            content = _strip(snippets[i]) if i < len(snippets) else ""
            out.append(
                {
                    "url": url,
                    "title": title,
                    "content": content,
                    "score": None,
                    "published_date": "",
                }
            )
        return out

    async def fetch_page(self, url: str) -> str:
        """DuckDuckGo has no page-extract endpoint — SSRF-guarded plain HTTP GET fallback."""
        return await plain_get_fetch(url)
