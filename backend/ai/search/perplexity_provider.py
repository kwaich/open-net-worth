from __future__ import annotations

from typing import Optional

import httpx

from backend.ai.search.base import SearchProvider

# Perplexity's Sonar search runs over an OpenAI-compatible chat-completions endpoint.
# A `sonar`/`sonar-pro` completion answers the query AND returns the sources it consulted
# as `search_results` (a list of {title, url, date, snippet}) — which is exactly the
# {url, title, content, …} shape this provider needs to emit. (Perplexity has no separate
# "search" endpoint; the search IS the chat call. See https://docs.perplexity.ai/.)
_BASE = "https://api.perplexity.ai"
_DEFAULT_MODEL = "sonar"


class PerplexityProvider(SearchProvider):
    """Web search via Perplexity's Sonar API (OpenAI-compatible chat completions).

    Dormant unless configured: the key is passed in (resolved by the search factory from
    SearchConfigStore). A `sonar` completion returns the sources it
    cited as ``search_results`` — those become the result rows. If a response carries no
    structured sources (older API tiers), the assistant message itself is returned as one
    synthetic result so the caller still gets the answer text, never a fabricated source.
    """

    plain_page_fetch = True  # fetch_page is the plain GET: og:image rides along

    def __init__(self, api_key: str, model: Optional[str] = None) -> None:
        if not api_key:
            raise ValueError("PerplexityProvider requires an API key")
        self.api_key = api_key
        self.model = model or _DEFAULT_MODEL

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    async def search_and_answer(self, query: str, max_results: int = 8) -> tuple[list[dict], str]:
        """Make one Sonar call and return (source_rows, answer_text).

        ``answer_text`` is the Sonar synthesized answer (may be an empty string when the
        response carries no assistant message). Use this instead of calling ``search()``
        when the caller also wants the Sonar summary — avoids a second API call.
        """
        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are a research assistant. Answer the user's query "
                        "concisely using current web sources."
                    ),
                },
                {"role": "user", "content": query},
            ],
            "web_search_options": {"search_context_size": "medium"},
        }
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"{_BASE}/chat/completions", json=payload, headers=self._headers()
            )
            resp.raise_for_status()
            data = resp.json()

        rows = self._extract_sources(data, max_results)
        answer = self._answer_text(data)
        return rows, answer

    async def search(self, query: str, max_results: int = 8) -> list[dict]:
        rows, answer = await self.search_and_answer(query, max_results)
        if rows:
            return rows

        # No structured sources — surface the answer text as a single synthetic row so the
        # caller still has something to read (never a fabricated url/source).
        if answer:
            return [{"url": "", "title": "Perplexity answer", "content": answer, "score": None}]
        return []

    async def fetch_page(self, url: str) -> str:
        """Perplexity has no page-extract endpoint — fall back to a plain HTTP GET."""
        from backend.ai.search.base import plain_get_fetch

        return await plain_get_fetch(url)

    # ── response parsing ──────────────────────────────────────────────────────────

    @staticmethod
    def _extract_sources(data: dict, max_results: int) -> list[dict]:
        """Map Perplexity's ``search_results`` (preferred) or legacy ``citations`` URL list
        into the standard result-row shape. Never raises on a missing/odd field."""
        out: list[dict] = []
        for r in data.get("search_results", []) or []:
            if not isinstance(r, dict):
                continue
            out.append(
                {
                    "url": r.get("url", "") or "",
                    "title": r.get("title", "") or "",
                    "content": r.get("snippet", "") or "",
                    "score": None,
                    "published_date": r.get("date", "") or "",
                }
            )
        if not out:
            # Legacy: a bare ``citations`` list of URL strings.
            for url in data.get("citations", []) or []:
                if isinstance(url, str) and url:
                    out.append({"url": url, "title": url, "content": "", "score": None})
        return out[:max_results]

    @staticmethod
    def _answer_text(data: dict) -> str:
        try:
            return (data["choices"][0]["message"]["content"] or "").strip()
        except (KeyError, IndexError, TypeError):
            return ""
