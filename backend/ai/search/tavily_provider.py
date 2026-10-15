from __future__ import annotations

import httpx

from backend.ai.search.base import SearchProvider

_BASE = "https://api.tavily.com"


class TavilyProvider(SearchProvider):
    """Web search via Tavily — optimised for AI agent use (returns extracted content, not raw HTML)."""

    def __init__(self, api_key: str) -> None:
        if not api_key:
            raise ValueError("TavilyProvider requires an API key")
        self.api_key = api_key

    async def search(self, query: str, max_results: int = 8) -> list[dict]:
        payload = {
            "api_key": self.api_key,
            "query": query,
            "max_results": max_results,
            "search_depth": "basic",
            "include_raw_content": False,
        }
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(f"{_BASE}/search", json=payload)
            resp.raise_for_status()
            data = resp.json()

        return [
            {
                "url": r.get("url", ""),
                "title": r.get("title", ""),
                "content": r.get("content", ""),
                "score": r.get("score"),
                "published_date": r.get("published_date", ""),
            }
            for r in data.get("results", [])
        ]

    async def search_news(self, query: str, max_results: int = 8) -> list[dict]:
        """Recent news articles via Tavily topic=news (real headlines + dates)."""
        payload = {
            "api_key": self.api_key,
            "query": query,
            "max_results": max_results,
            "topic": "news",
            "days": 30,
            "search_depth": "basic",
            "include_raw_content": False,
        }
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(f"{_BASE}/search", json=payload)
            resp.raise_for_status()
            data = resp.json()

        return [
            {
                "url": r.get("url", ""),
                "title": r.get("title", ""),
                "content": r.get("content", ""),
                "score": r.get("score"),
                "published_date": r.get("published_date", ""),
            }
            for r in data.get("results", [])
        ]

    async def fetch_page(self, url: str) -> str:
        payload = {
            "api_key": self.api_key,
            "urls": [url],
        }
        async with httpx.AsyncClient(timeout=60) as client:
            try:
                resp = await client.post(f"{_BASE}/extract", json=payload)
                resp.raise_for_status()
                data = resp.json()
            except Exception as exc:
                return f"[fetch failed: {exc}]"

        results = data.get("results", [])
        if results:
            return results[0].get("raw_content", "") or results[0].get("content", "")
        return "[no content extracted]"
