"""Regression tests for in-history tool-result compaction.

Age-based decay, supersession shrink, AND free-text head-truncation are all RETIRED
(2026-08-27, owner-directed) — see docs/adr/2026-08-27-age-decay-and-supersession-shrink-are-both-retired.md
and its follow-up. Everything inside the sliding window (MAX_HISTORY_MESSAGES) is sent
fully verbatim, always, regardless of tool or size. Only fold (an LLM reading real
content and producing a real summary) still compacts anything, and only for the
portion OLDER than the window.
"""

from __future__ import annotations

from backend.ai import assistant


def _assistant_tool_use(tool_use_id: str, name: str) -> dict:
    return {
        "role": "assistant",
        "content": [{"type": "tool_use", "id": tool_use_id, "name": name, "input": {}}],
    }


def _tool_result(tool_use_id: str, content: str) -> dict:
    return {
        "role": "user",
        "content": [{"type": "tool_result", "tool_use_id": tool_use_id, "content": content}],
    }


def _result_content(prepared: list[dict], tool_use_id: str) -> str | None:
    for msg in prepared:
        if msg.get("role") == "user" and isinstance(msg.get("content"), list):
            for block in msg["content"]:
                if block.get("type") == "tool_result" and block.get("tool_use_id") == tool_use_id:
                    return block.get("content")
    return None


def test_web_search_result_is_never_truncated():
    """Free-text head-truncation is retired — a huge web_search/fetch_page result
    survives in the window exactly as returned, no cap, no '[truncated]' marker."""
    big = "RESULT " + ("w" * 50_000)
    history = [
        {"role": "user", "content": "search"},
        _assistant_tool_use("s1", "web_search"),
        _tool_result("s1", big),
    ]
    prepared = assistant._prepare_messages(history)
    assert _result_content(prepared, "s1") == big


def test_fetch_page_result_is_never_truncated():
    big = "PAGE " + ("q" * 50_000)
    history = [
        {"role": "user", "content": "read it"},
        _assistant_tool_use("f1", "fetch_page"),
        _tool_result("f1", big),
    ]
    prepared = assistant._prepare_messages(history)
    assert _result_content(prepared, "f1") == big


def test_orphan_tool_result_still_dropped():
    """A tool_result whose tool_use was evicted out of the window is still dropped
    (the long-standing window-boundary invariant) — unrelated to truncation."""
    history = [
        # Orphan result with no matching tool_use in the window.
        _tool_result("ghost", "ORPHAN"),
        {"role": "user", "content": "hello"},
    ]
    prepared = assistant._prepare_messages(history)
    assert _result_content(prepared, "ghost") is None
