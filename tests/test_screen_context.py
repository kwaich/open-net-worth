"""Screen-aware context — service formatter + per-turn fetch budget + the get_screen_detail tool.

Design: docs/superpowers/specs/2026-06-24-screen-aware-context-design.md.
"""

from __future__ import annotations

import asyncio
import json

from backend.models.screen import ScreenHandle, ScreenSnapshot
from backend.services import screen_context

# ── formatter ─────────────────────────────────────────────────────────────────


def test_format_includes_title_facts_and_handle_manifest():
    snap = ScreenSnapshot(
        id="asset-detail",
        title="NVIDIA Corporation",
        view_state={"tab": "analysis"},
        facts={"net_worth": "S$123,456", "movers": "NVDA +3.1%"},
        as_of="2026-06-24T10:00",
        handles=[
            ScreenHandle(
                id="news-0", type="news_story", label="Reuters — NVDA Q3", descriptor="full article"
            ),
            ScreenHandle(
                id="price-1y",
                type="price_series",
                label="Price history – 1Y",
                descriptor="~250 closes",
            ),
        ],
    )
    block = screen_context.format_screen_context(snap)
    assert "NVIDIA Corporation" in block
    assert "as of 2026-06-24T10:00" in block
    assert "net_worth=S$123,456" in block
    assert "tab=analysis" in block
    assert "get_screen_detail" in block  # steers the model to the on-demand path
    assert "- news-0 (news_story): Reuters — NVDA Q3 — full article" in block
    assert "price-1y" in block


def test_format_empty_snapshot_is_blank():
    assert screen_context.format_screen_context(None) == ""
    assert screen_context.format_screen_context(ScreenSnapshot()) == ""


def test_format_bounds_visible_text():
    snap = ScreenSnapshot(title="X", visible_text="z" * 5000)
    block = screen_context.format_screen_context(snap)
    # visible text is capped well under the raw length
    assert "z" * 5000 not in block
    assert "visible:" in block


# ── per-turn fetch budget ─────────────────────────────────────────────────────


def test_fetch_budget_caps_per_turn():
    screen_context.reset_fetch_budget()
    allowed = [
        screen_context.consume_fetch_budget()
        for _ in range(screen_context.MAX_HANDLES_PER_TURN_FETCH + 2)
    ]
    assert (
        allowed[: screen_context.MAX_HANDLES_PER_TURN_FETCH]
        == [True] * screen_context.MAX_HANDLES_PER_TURN_FETCH
    )
    assert allowed[screen_context.MAX_HANDLES_PER_TURN_FETCH :] == [False, False]


def test_fetch_budget_zero_before_reset():
    # A path that never reset the budget (e.g. headless) gets no fetches.
    from contextvars import copy_context

    def _fresh():
        # run in a fresh context so a prior reset in this process doesn't leak in
        return screen_context.consume_fetch_budget()

    assert copy_context().run(_fresh) is False


# ── the tool ──────────────────────────────────────────────────────────────────


class _FakeProvider:
    async def fetch_page(self, url: str) -> str:
        return f"ARTICLE BODY for {url}"


def _run_tool(handle_id: str):
    from backend.ai.tools.screen import get_screen_detail

    raw, _ = asyncio.run(get_screen_detail({"handle_id": handle_id}))
    return json.loads(raw)


def test_tool_errors_without_session():
    from backend.core.chat_session import clear_session_id

    clear_session_id()
    out = _run_tool("news-0")
    assert "error" in out


def test_tool_hydrates_news_url(monkeypatch):
    from backend.ai import assistant
    from backend.core.chat_session import set_session_id

    sid = "sess-screen-test"
    assistant.upsert_session_meta(
        sid,
        screen_handles=[
            {
                "id": "news-0",
                "type": "news_story",
                "label": "Reuters",
                "source": "https://example.com/nvda",
            }
        ],
    )
    set_session_id(sid)
    screen_context.reset_fetch_budget()
    monkeypatch.setattr("backend.ai.search.get_search_provider", lambda: _FakeProvider())

    out = _run_tool("news-0")
    assert out["url"] == "https://example.com/nvda"
    assert "ARTICLE BODY" in out["content"]
    assert out["source"] == "Reuters"


def test_tool_missing_handle_errors(monkeypatch):
    from backend.ai import assistant
    from backend.core.chat_session import set_session_id

    sid = "sess-screen-test-2"
    assistant.upsert_session_meta(sid, screen_handles=[{"id": "news-0", "source": "https://x.com"}])
    set_session_id(sid)
    screen_context.reset_fetch_budget()
    out = _run_tool("does-not-exist")
    assert "error" in out


def test_tool_blocks_unsafe_url(monkeypatch):
    from backend.ai import assistant
    from backend.core.chat_session import set_session_id

    sid = "sess-screen-ssrf"
    assistant.upsert_session_meta(
        sid,
        screen_handles=[
            {
                "id": "h",
                "type": "news_story",
                "label": "x",
                "source": "http://169.254.169.254/latest",
            }
        ],
    )
    set_session_id(sid)
    screen_context.reset_fetch_budget()
    out = _run_tool("h")
    # SSRF guard rejects the metadata IP before any fetch
    assert "blocked" in out["content"].lower()


def test_tool_hydrates_transactions_register():
    """The Cash screen's 'Full transaction register' handle (source 'transactions:recent') fetches
    the rows behind the register — incl. those below the fold — server-side."""
    from backend.ai import assistant
    from backend.core.chat_session import set_session_id
    from backend.db.transaction_store import TransactionStore

    txn = TransactionStore()
    txn.insert(
        {
            "id": "txn-reg-1",
            "date": "2026-06-20",
            "kind": "expense",
            "description": "Corner Coffee",
            "from_amount": 5.0,
            "from_currency": "SGD",
        }
    )
    sid = "sess-screen-txns"
    assistant.upsert_session_meta(
        sid,
        screen_handles=[
            {
                "id": "txn-register",
                "type": "table",
                "label": "Full transaction register",
                "source": "transactions:recent",
            }
        ],
    )
    set_session_id(sid)
    screen_context.reset_fetch_budget()
    try:
        out = _run_tool("txn-register")
        assert "Corner Coffee" in out["content"]
        assert out["source"] == "Full transaction register"
    finally:
        txn.delete("txn-reg-1")


# ── request wiring ─────────────────────────────────────────────────────────────


def test_chat_request_accepts_screen():
    from backend.api.chat import ChatRequest

    req = ChatRequest(
        messages=[{"role": "user", "content": "what am I looking at?"}],
        screen={
            "id": "asset-detail",
            "title": "NVIDIA Corporation",
            "facts": {"movers": "NVDA +3.1%"},
            "handles": [{"id": "news-0", "type": "news_story", "source": "https://x.com/a"}],
        },
    )
    assert req.screen is not None
    assert req.screen.title == "NVIDIA Corporation"
    assert req.screen.handles[0].id == "news-0"
    # absent screen stays None (default Lucius path unchanged)
    assert ChatRequest(messages=[{"role": "user", "content": "hi"}]).screen is None
