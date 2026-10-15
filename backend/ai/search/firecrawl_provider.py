from __future__ import annotations

import httpx

from backend.ai.search.base import SearchProvider

# https://docs.firecrawl.dev/api-reference/endpoint/search + /scrape — v2, Bearer auth.
_BASE = "https://api.firecrawl.dev/v2"


class FirecrawlProvider(SearchProvider):
    """Web search + page extraction via Firecrawl — the same "clean markdown, not raw HTML"
    positioning as Tavily, but built specifically around scraping (JS-rendered pages
    included), so ``fetch_page`` is native here too rather than the plain-GET fallback."""

    def __init__(self, api_key: str) -> None:
        if not api_key:
            raise ValueError("FirecrawlProvider requires an API key")
        self.api_key = api_key

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    @staticmethod
    def _rows(results: list, max_results: int) -> list[dict]:
        out: list[dict] = []
        for r in results or []:
            if not isinstance(r, dict):
                continue
            out.append(
                {
                    "url": r.get("url", "") or "",
                    "title": r.get("title", "") or "",
                    "content": r.get("description", "") or "",
                    "score": None,
                }
            )
        return out[:max_results]

    async def search(self, query: str, max_results: int = 8) -> list[dict]:
        payload = {"query": query, "limit": max_results, "sources": [{"type": "web"}]}
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(f"{_BASE}/search", json=payload, headers=self._headers())
            resp.raise_for_status()
            data = resp.json()

        return self._rows((data.get("data") or {}).get("web"), max_results)

    async def search_news(self, query: str, max_results: int = 8) -> list[dict]:
        """Recent news via Firecrawl's ``sources: [{"type": "news"}]`` search vertical."""
        payload = {"query": query, "limit": max_results, "sources": [{"type": "news"}]}
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(f"{_BASE}/search", json=payload, headers=self._headers())
            resp.raise_for_status()
            data = resp.json()

        return self._rows((data.get("data") or {}).get("news"), max_results)

    async def fetch_page(self, url: str) -> str:
        payload = {"url": url}
        async with httpx.AsyncClient(timeout=60) as client:
            try:
                resp = await client.post(f"{_BASE}/scrape", json=payload, headers=self._headers())
                resp.raise_for_status()
                data = resp.json()
            except Exception as exc:
                return f"[fetch failed: {exc}]"

        markdown = (data.get("data") or {}).get("markdown") or ""
        return markdown if markdown.strip() else "[no content extracted]"
