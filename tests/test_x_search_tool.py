"""x_search tool — X/Twitter social search via the active web-search provider.

x_search is a thin tool over whatever web-search provider is active: it scopes the query to
x.com/twitter.com and returns the same result shape as web_search, so it's zero-config (no
dedicated X/Twitter key). These tests assert the site-scope is applied and the shape passes
through verbatim. x_search's classification as an untrusted-content feed is pinned in
tests/test_unattended_tool_safety.py; its output fencing in tests/test_misc_tool_fence.py.

Offline + isolated: the provider is stubbed (no network).
"""

from __future__ import annotations

import asyncio
import json

from backend.ai.tools.misc import x_search


class _StubProvider:
    def __init__(self, rows):
        self._rows = rows
        self.queries: list[str] = []
        self.max_results: list[int] = []

    async def search(self, query: str, max_results: int = 8) -> list[dict]:
        self.queries.append(query)
        self.max_results.append(max_results)
        return self._rows

    async def fetch_page(self, url: str) -> str:  # pragma: no cover - not exercised here
        return ""


_ROWS = [
    {"url": "https://x.com/u/status/1", "title": "post one", "content": "bullish on $NVDA"},
    {"url": "https://twitter.com/u/status/2", "title": "post two", "content": "selling"},
]


def _run(monkeypatch, input_data: dict, rows=_ROWS):
    stub = _StubProvider(rows)
    monkeypatch.setattr("backend.ai.tools.misc.get_search_provider", lambda: stub)
    text, proposed = asyncio.run(x_search(input_data))
    return text, proposed, stub


def test_x_search_scopes_query_to_x(monkeypatch):
    text, proposed, stub = _run(monkeypatch, {"query": "$NVDA"})
    # The query handed to the provider includes the X site scope (no provider hardcoded).
    assert len(stub.queries) == 1
    scoped = stub.queries[0]
    assert "$NVDA" in scoped
    assert "site:x.com" in scoped
    assert "site:twitter.com" in scoped


def test_x_search_returns_same_shape_as_web_search(monkeypatch):
    text, proposed, _ = _run(monkeypatch, {"query": "tesla"})
    assert proposed is None  # read tool — no proposed change
    rows = json.loads(text)
    # url is structural and passes through verbatim; title/content are third-party social
    # text a poster fully controls, so they're sanitized + nonce-fenced as untrusted (see
    # tests/test_misc_tool_fence.py) rather than passed through raw.
    assert len(rows) == len(_ROWS)
    for row, orig in zip(rows, _ROWS):
        assert row["url"] == orig["url"]
        assert orig["title"] in row["title"]
        assert orig["content"] in row["content"]


def test_x_search_caps_max_results(monkeypatch):
    _, _, stub = _run(monkeypatch, {"query": "q", "max_results": 99})
    assert stub.max_results == [15]  # capped at 15, mirroring web_search


def test_x_search_defaults_max_results(monkeypatch):
    _, _, stub = _run(monkeypatch, {"query": "q"})
    assert stub.max_results == [8]


def test_x_search_empty_results(monkeypatch):
    text, _, _ = _run(monkeypatch, {"query": "q"}, rows=[])
    assert json.loads(text) == []


def test_x_search_is_registered():
    from backend.ai.tools import TOOL_SCHEMAS

    names = {t["name"] for t in TOOL_SCHEMAS}
    assert "x_search" in names
