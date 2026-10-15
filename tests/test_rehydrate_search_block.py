"""Regression: a web_search call rehydrates as the same rich "search" chip the live
stream shows, not a generic "web_search ▾" tool pill.

Live report 2026-08-10: the query+sources chip degraded to a plain generic tool pill
after the turn finished (streamed live, then reloaded/rehydrated). Root cause:
rehydrate_messages reconstructed EVERY tool_use content block — including web_search —
as a generic {"type": "tool", ...} block. The live SSE path never does this for
web_search (chatReducer.ts's tool_use_start handler explicitly excludes it — it uses
search_start/search_results instead), so a reload showed a DIFFERENT conversation than
the one that streamed in. The result data (title/url per source) is persisted in full
in the tool_result, so nothing is actually lost — it just wasn't being read back.
"""

from __future__ import annotations

from backend.ai import assistant


def test_a_web_search_call_rehydrates_as_a_search_block_not_a_generic_tool_pill(
    monkeypatch,
):
    history = [
        {"role": "user", "content": "any news about orcle?"},
        {
            "role": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "id": "tu-1",
                    "name": "web_search",
                    "input": {"query": "Oracle ORCL news August 2026"},
                }
            ],
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "tu-1",
                    "content": (
                        '[{"title": "Oracle Q1 beat", "url": "https://example.com/a"}, '
                        '{"title": "ORCL guidance cut", "url": "https://example.com/b"}]'
                    ),
                }
            ],
        },
        {
            "role": "assistant",
            "content": [{"type": "text", "text": "Mixed picture for ORCL."}],
        },
    ]
    monkeypatch.setattr(assistant, "_load_session", lambda sid: history)

    messages = assistant.rehydrate_messages("sess-1")

    asst = [m for m in messages if m["role"] == "assistant"]
    assert len(asst) == 1
    blocks = asst[0]["blocks"]
    assert not any(b["type"] == "tool" for b in blocks), "no generic tool pill for web_search"
    search_blocks = [b for b in blocks if b["type"] == "search"]
    assert len(search_blocks) == 1
    block = search_blocks[0]
    assert block["query"] == "Oracle ORCL news August 2026"
    assert block["loading"] is False
    assert block["results"] == [
        {"title": "Oracle Q1 beat", "url": "https://example.com/a"},
        {"title": "ORCL guidance cut", "url": "https://example.com/b"},
    ]


def test_a_web_search_call_with_a_malformed_result_leaves_an_empty_results_list(
    monkeypatch,
):
    history = [
        {"role": "user", "content": "any news?"},
        {
            "role": "assistant",
            "content": [
                {"type": "tool_use", "id": "tu-1", "name": "web_search", "input": {"query": "q"}}
            ],
        },
        {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "tu-1", "content": '{"error": "timed out"}'}
            ],
        },
        {"role": "assistant", "content": [{"type": "text", "text": "Couldn't find anything."}]},
    ]
    monkeypatch.setattr(assistant, "_load_session", lambda sid: history)

    messages = assistant.rehydrate_messages("sess-1")

    asst = [m for m in messages if m["role"] == "assistant"]
    block = next(b for b in asst[0]["blocks"] if b["type"] == "search")
    assert block["query"] == "q"
    assert block["loading"] is False
    assert block["results"] == []
