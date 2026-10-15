"""Transcript payload elision + backward paging (2026-08-12 perf batch).

Measured on real data (733 sessions): tool results were 85–95% of every heavy
``tail=30`` transcript payload — the worst conversation shipped 2.27MB of tool
results inside a 2.4MB response, single ``fetch_page`` results ran to 767KB, while
3.5k of 4.8k results were under 1KB. The UI renders a result only inside a
tap-to-open pane, so those bytes were paid on every open and almost never looked at.

The list endpoint now elides any tool result/input above ``_ELIDE_TOOL_CHARS``
(replaced by ``resultElided``/``inputElided`` char counts); a tapped pill fetches
``GET /api/sessions/{id}/tool-results/{tool_use_id}`` for the full block. ``before=C``
pages backwards from the earliest message a client holds — "Show earlier" used to
dead-end at the fetched tail.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

import backend.ai.assistant as assistant
import backend.api.chat as chat_mod
from backend.api.chat import _ELIDE_TOOL_CHARS, _elide_tool_payloads

BIG = "x" * (_ELIDE_TOOL_CHARS + 1)
SMALL = "y" * 100


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(chat_mod.router)
    return TestClient(app)


def _tool_block(tool_id: str, result: str | None, input_: dict | None = None) -> dict:
    b = {"type": "tool", "name": "fetch_page", "id": tool_id, "status": "done"}
    if input_ is not None:
        b["input"] = input_
    if result is not None:
        b["result"] = result
    return b


def test_oversized_results_and_inputs_are_elided_small_ones_kept():
    messages = [
        {
            "id": "s-1",
            "role": "assistant",
            "text": "",
            "blocks": [
                _tool_block("tu-big", BIG, {"url": "https://a"}),
                _tool_block("tu-small", SMALL, {"url": "https://b"}),
                _tool_block("tu-biginput", SMALL, {"blob": BIG}),
                {"type": "text", "content": "done"},
            ],
        }
    ]

    out = _elide_tool_payloads(messages)

    big, small, biginput, text = out[0]["blocks"]
    assert "result" not in big and big["resultElided"] == len(BIG)
    assert big["input"] == {"url": "https://a"}  # small input stays
    assert small["result"] == SMALL and "resultElided" not in small
    assert "input" not in biginput and biginput["inputElided"] > _ELIDE_TOOL_CHARS
    assert biginput["result"] == SMALL
    assert text == {"type": "text", "content": "done"}


def test_elision_never_mutates_the_rehydrated_messages():
    block = _tool_block("tu-1", BIG)
    messages = [{"id": "s-1", "role": "assistant", "text": "", "blocks": [block]}]

    _elide_tool_payloads(messages)

    assert block["result"] == BIG, "elision must copy, not mutate — rehydrate output is shared"


def test_a_pending_approval_block_is_not_marked_elided():
    # "No result" on a pending block means AWAITING, not elided — the frontend keys its
    # fetch stub off resultElided, so a result-less block must pass through untouched.
    pending = {"type": "tool", "name": "delete_account", "id": "tu-p", "status": "done"}
    out = _elide_tool_payloads([{"id": "s-1", "role": "assistant", "blocks": [pending]}])
    assert out[0]["blocks"][0] == pending


def _fake_transcript(n_msgs: int) -> list[dict]:
    return [
        {"id": f"sess-{i + 1}", "role": "user" if i % 2 == 0 else "assistant", "text": f"m{i + 1}"}
        for i in range(n_msgs)
    ]


def test_before_pages_backwards_and_slices_are_byte_identical(monkeypatch):
    monkeypatch.setattr(assistant, "rehydrate_messages", lambda sid: _fake_transcript(10))
    client = _client()

    full = client.get("/api/sessions/sess/messages").json()
    page = client.get("/api/sessions/sess/messages", params={"tail": 3, "before": 8}).json()

    assert page["total"] == 10
    # counter < 8 is messages 1–7; last 3 of those are 5, 6, 7 — byte-identical to the full fetch
    assert page["messages"] == full["messages"][4:7]
    assert [m["id"] for m in page["messages"]] == ["sess-5", "sess-6", "sess-7"]


def test_before_at_the_start_returns_empty_not_an_error(monkeypatch):
    monkeypatch.setattr(assistant, "rehydrate_messages", lambda sid: _fake_transcript(4))
    client = _client()
    page = client.get("/api/sessions/sess/messages", params={"tail": 30, "before": 1}).json()
    assert page["messages"] == []
    assert page["total"] == 4


def test_conversation_has_no_skill_deactivation_endpoint():
    client = _client()

    response = client.delete("/api/sessions/sess/skills/budget-review")

    assert response.status_code == 404


def test_endpoint_elides_by_default_and_full_opts_out(monkeypatch):
    msgs = [{"id": "s-1", "role": "assistant", "text": "", "blocks": [_tool_block("tu-1", BIG)]}]
    monkeypatch.setattr(assistant, "rehydrate_messages", lambda sid: msgs)
    client = _client()

    elided = client.get("/api/sessions/s/messages").json()["messages"][0]["blocks"][0]
    assert "result" not in elided and elided["resultElided"] == len(BIG)

    full = client.get("/api/sessions/s/messages", params={"full": True}).json()
    assert full["messages"][0]["blocks"][0]["result"] == BIG


def test_tool_detail_endpoint_returns_the_full_block(monkeypatch):
    history = [
        {"role": "user", "content": "fetch it"},
        {
            "role": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "id": "tu-1",
                    "name": "fetch_page",
                    "input": {"url": "https://a"},
                }
            ],
        },
        {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "tu-1", "content": BIG}],
        },
    ]
    monkeypatch.setattr(assistant, "_load_session", lambda sid: history)
    client = _client()

    detail = client.get("/api/sessions/s/tool-results/tu-1").json()
    assert detail == {"name": "fetch_page", "input": {"url": "https://a"}, "result": BIG}

    assert client.get("/api/sessions/s/tool-results/tu-nope").status_code == 404


def test_tool_detail_for_a_pending_call_has_no_result_key(monkeypatch):
    history = [
        {
            "role": "assistant",
            "content": [{"type": "tool_use", "id": "tu-1", "name": "delete_account", "input": {}}],
        }
    ]
    monkeypatch.setattr(assistant, "_load_session", lambda sid: history)
    client = _client()
    detail = client.get("/api/sessions/s/tool-results/tu-1").json()
    assert detail == {"name": "delete_account", "input": {}}
