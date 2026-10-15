"""Retry replaces the failed turn's user message; it never persists a second copy.

Persona QA round 5: after Retry the same user message sat in the transcript twice
(session_messages ids 8 and 9, identical) and rendered twice on reload.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import backend.ai.assistant as assistant
import backend.ai.live_sessions as live_sessions
import backend.ai.turns as turns
import backend.api.chat as chat_mod

SID = "retry-sess"
ASKED = {"role": "user", "content": "add my accounts"}
GOOD_TURN = [
    {"role": "user", "content": "hello"},
    {"role": "assistant", "content": [{"type": "text", "text": "hi there"}]},
]
TOOL_WORK = [
    {"role": "assistant", "content": [{"type": "tool_use", "id": "t1", "name": "x", "input": {}}]},
    {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "ok"}]},
]


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    monkeypatch.setattr("backend.ai.conversation.build_system_prompt", lambda **_kwargs: "SYS")
    live_sessions._reset_for_tests()
    assistant.SESSIONS.pop(SID, None)
    turns.set_inline_for_tests(True)
    yield
    assistant.SESSIONS.pop(SID, None)
    live_sessions._reset_for_tests()
    turns.set_inline_for_tests(False)
    turns._turns.clear()
    turns._by_session.clear()


def _kept(session_id: str) -> list[dict]:
    return [{"role": m["role"], "content": m["content"]} for m in assistant.SESSIONS[session_id]]


def _seed(history: list[dict], status: str = "error") -> None:
    assistant.SESSIONS[SID] = [dict(m) for m in history]
    assistant.upsert_session_meta(SID, last_turn_status=status, last_turn_error="boom")


def test_a_failed_turn_with_no_tool_work_is_dropped():
    _seed([*GOOD_TURN, ASKED])
    assert assistant.discard_failed_turn(SID, "add my accounts") is True
    assert _kept(SID) == GOOD_TURN


def test_a_partial_text_reply_of_the_failed_turn_goes_with_it():
    partial = {"role": "assistant", "content": [{"type": "text", "text": "Let me"}]}
    _seed([*GOOD_TURN, ASKED, partial])
    assert assistant.discard_failed_turn(SID, "add my accounts") is True
    assert _kept(SID) == GOOD_TURN


def test_a_user_message_with_an_attachment_matches_on_the_typed_words():
    asked = {
        "role": "user",
        "content": [
            {"type": "attachment_ref", "attachment_id": "a1"},
            {"type": "text", "text": "add my accounts"},
        ],
    }
    _seed([asked])
    assert assistant.discard_failed_turn(SID, "add my accounts") is True
    assert _kept(SID) == []


def test_a_failed_turn_that_ran_tools_keeps_its_history():
    _seed([ASKED, *TOOL_WORK])
    assert assistant.discard_failed_turn(SID, "add my accounts") is False
    assert len(assistant.SESSIONS[SID]) == 3


def test_a_different_message_is_never_dropped():
    _seed([*GOOD_TURN, ASKED])
    assert assistant.discard_failed_turn(SID, "something else") is False
    assert len(assistant.SESSIONS[SID]) == 3


def test_a_turn_that_succeeded_is_never_dropped():
    _seed([*GOOD_TURN, ASKED], status="ok")
    assert assistant.discard_failed_turn(SID, "add my accounts") is False
    assert len(assistant.SESSIONS[SID]) == 3


def _post(retry: bool, seen: list[list[dict]], monkeypatch) -> None:
    async def fake_run(session_id, new_messages, system, model=None, effort="medium", **kw):
        history = assistant.SESSIONS[session_id]
        history.extend(new_messages)
        seen.append([dict(m) for m in history])
        yield {"type": "turn_done", "usage": {}}

    monkeypatch.setattr(assistant, "run", fake_run)
    app = FastAPI()
    app.include_router(chat_mod.router)
    res = TestClient(app).post(
        "/api/chat",
        json={
            "session_id": SID,
            "messages": [{"role": "user", "content": "add my accounts"}],
            "retry": retry,
        },
    )
    assert res.status_code == 202


def test_a_retried_post_leaves_exactly_one_copy_of_the_message(monkeypatch):
    _seed([*GOOD_TURN, ASKED])
    seen: list[list[dict]] = []
    _post(True, seen, monkeypatch)
    assert [m["content"] for m in seen[0]].count("add my accounts") == 1


def test_a_plain_post_still_appends(monkeypatch):
    _seed([*GOOD_TURN, ASKED])
    seen: list[list[dict]] = []
    _post(False, seen, monkeypatch)
    assert [m["content"] for m in seen[0]].count("add my accounts") == 2
