from __future__ import annotations

import httpx

from backend.ai.search.base import SearchProvider, plain_get_fetch

# Serper.dev — fast, low-cost Google SERP results. Auth is the X-API-KEY header; the body is
# {"q": query, "num": n}. https://serper.dev/
_BASE = "https://google.serper.dev/search"
# The news vertical returns real article headlines (not quote/overview pages), each with a
# source name, publish date, and thumbnail image.
_NEWS = "https://google.serper.dev/news"


class SerperProvider(SearchProvider):
    """Web search via serper.dev (Google SERP). Verified June 2026."""

    plain_page_fetch = True  # fetch_page is the plain GET: og:image rides along

    def __init__(self, api_key: str) -> None:
        if not api_key:
            raise ValueError("SerperProvider requires an API key")
        self.api_key = api_key

    def _headers(self) -> dict:
        return {"X-API-KEY": self.api_key, "Content-Type": "application/json"}

    async def search(self, query: str, max_results: int = 8) -> list[dict]:
        payload = {"q": query, "num": max(1, min(max_results, 20))}
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(_BASE, json=payload, headers=self._headers())
            resp.raise_for_status()
            data = resp.json()

        out: list[dict] = []
        for r in data.get("organic", []) or []:
            if not isinstance(r, dict):
                continue
            out.append(
                {
                    "url": r.get("link", "") or "",
                    "title": r.get("title", "") or "",
                    "content": r.get("snippet", "") or "",
                    "score": None,
                    "published_date": r.get("date", "") or "",
                }
            )
        return out[:max_results]

    async def search_news(self, query: str, max_results: int = 8) -> list[dict]:
        """Real news articles via serper.dev /news (title, snippet, date, source, image)."""
        payload = {"q": query, "num": max(1, min(max_results, 20))}
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(_NEWS, json=payload, headers=self._headers())
            resp.raise_for_status()
            data = resp.json()

        out: list[dict] = []
        for r in data.get("news", []) or []:
            if not isinstance(r, dict):
                continue
            out.append(
                {
                    "url": r.get("link", "") or "",
                    "title": r.get("title", "") or "",
                    "content": r.get("snippet", "") or "",
                    "score": None,
                    "published_date": r.get("date", "") or "",
                    "source": r.get("source", "") or "",
                    "image": r.get("imageUrl") or None,
                }
            )
        return out[:max_results]

    async def fetch_page(self, url: str) -> str:
        """Serper has no page-extract endpoint — plain HTTP GET fallback."""
        return await plain_get_fetch(url)
