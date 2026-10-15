"""B6 (concurrent-conversations RFC, acceptance ``docs/qa/acceptance/concurrent-conversations.md``):
deleting a conversation with a turn in flight must ABORT that turn, not orphan it.

During the Phase 3 merge, ``ChatPanel.tsx``'s ``handleDeleteConversation`` was found calling a
dead ``chatSessions.getSlice(id).abort?.abort()`` — the ``AbortController`` mechanism Phase 3
retired — so the frontend's cancel-on-delete silently no-op'd. It was pointed at
``POST /api/chat/{id}/stop`` instead, but that only cancels a turn the FRONTEND asks about —
this file tests the half that actually matters: does ``DELETE /api/chat/{session_id}``
(``backend.api.chat.clear_chat`` -> ``assistant.clear_interactive_session`` ->
``assistant.clear_session``) itself reach into ``backend.ai.turns`` and cancel a turn currently
registered for that session?

As of this writing it does NOT: ``clear_session`` pops the in-memory ``SESSIONS`` entry and
deletes the on-disk transcript file, but never calls ``turns.cancel()``. The turn's task (and
the ``history`` list object it is still holding a reference to from BEFORE the delete) keeps
running untouched, and every subsequent ``_save_session`` call inside that turn resurrects the
just-deleted transcript file on disk — a data-integrity bug, not a UI nit: a user who deletes a
conversation mid-turn sees it vanish, then watches it silently reappear.

Hermetic: no network, no LLM — ``assistant.run`` is replaced with a scripted async generator
that hangs until explicitly released, matching ``tests/test_chat_live_stream.py``'s
``test_stop_cancels_a_running_turn`` (the sibling test for the ``/stop`` endpoint this file
mirrors for ``DELETE``).
"""

from __future__ import annotations

import asyncio
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import backend.ai.assistant as assistant
import backend.ai.live_sessions as live_sessions
import backend.ai.turns as turns
import backend.api.chat as chat_mod
from backend.api.chat import ChatRequest
from tests._asgi import make_request


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    # Sessions (assistant._sessions_dir) and PendingPlanStore are both DB-backed now
    # (Wave 5E / 5D2b) — no more per-store file path to monkeypatch; both resolve
    # against the shared default engine, cleared after every test.
    monkeypatch.setattr("backend.ai.conversation.build_system_prompt", lambda **_kwargs: "SYS")
    live_sessions._reset_for_tests()
    turns.set_inline_for_tests(False)
    yield
    live_sessions._reset_for_tests()
    turns.set_inline_for_tests(False)
    turns._turns.clear()
    turns._by_session.clear()


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(chat_mod.router)
    return TestClient(app)


async def _spawn_hanging_turn(monkeypatch, session_id: str) -> tuple[str, asyncio.Event]:
    """Spawn a turn (via the real ``POST /api/chat`` route function) that emits one delta
    then hangs until cancelled — the same shape ``test_stop_cancels_a_running_turn`` uses."""
    reached = asyncio.Event()

    async def fake_run(session_id, new_messages, system, model=None, effort="medium", **kw):
        yield {"type": "text_delta", "text": "hi"}
        reached.set()
        await asyncio.Event().wait()  # hangs forever — only a cancel ends this

    monkeypatch.setattr(assistant, "run", fake_run)

    req = ChatRequest(session_id=session_id, messages=[{"role": "user", "content": "hi"}])
    response = await chat_mod.chat(req, make_request())
    assert response.status_code == 202
    turn_id = json.loads(bytes(response.body))["turn_id"]

    await asyncio.wait_for(reached.wait(), timeout=1)
    return turn_id, reached


# ── B6: DELETE must cancel a turn currently registered for the session ──────────────────


async def test_delete_conversation_cancels_the_running_turn(monkeypatch):
    """The server-side half of B6: ``DELETE /api/chat/{id}`` while a turn is registered
    and running for that session must cancel the turn — it must not be left orphaned,
    still owning the (now-deleted) session's turn registry entry."""
    sid = "s-delete-midturn"
    turn_id, _reached = await _spawn_hanging_turn(monkeypatch, sid)

    assert turns.executing_turn_id(sid) == turn_id, "the turn must be registered before delete"
    assert live_sessions.is_live(sid), "the turn must be live before delete"

    c = _client()
    r = c.delete(f"/api/chat/{sid}")
    assert r.status_code == 200
    assert r.json() == {"ok": True}

    drained = await turns.adrain_for_tests(sid, timeout=1)
    assert drained, (
        "B6 REGRESSION: DELETE /api/chat/{id} did not cancel the turn that was running "
        "for this session — turns._by_session still holds it a full second after the "
        "delete returned. A turn left running after its conversation is deleted keeps "
        "writing (assistant._save_session) through the delete, resurrecting the "
        "just-deleted transcript on disk — a data-integrity bug, not a UI nit."
    )
    assert turns._turns.get(turn_id) is None, "the turn handle must be unregistered"
    assert not live_sessions.is_live(sid), "the live episode must be closed"


async def test_delete_conversation_stamps_the_turn_cancelled_not_left_running(monkeypatch):
    """A second angle on the same defect: even setting aside the registry, the deleted
    conversation's OWN session-meta row must never be left reporting ``last_turn_status
    == "running"`` forever once its turn has actually been cancelled by the delete."""
    sid = "s-delete-status"
    turn_id, _reached = await _spawn_hanging_turn(monkeypatch, sid)

    c = _client()
    r = c.delete(f"/api/chat/{sid}")
    assert r.status_code == 200

    assert await turns.adrain_for_tests(sid, timeout=1), (
        "the turn was never cancelled by the delete, so its status can never resolve "
        "away from 'running'"
    )


async def test_delete_conversation_cancels_the_turn_via_assistant_clear_interactive_session(
    monkeypatch,
):
    """Same defect, driven through ``assistant.clear_interactive_session`` directly rather
    than the HTTP layer — isolates the assertion to the actual choke point
    ``backend.api.chat.clear_chat`` calls, in case a future refactor moves delete off the
    HTTP handler (e.g. a connector or MCP delete path) without going through the router."""
    sid = "s-delete-direct"
    turn_id, _reached = await _spawn_hanging_turn(monkeypatch, sid)

    assert turns.executing_turn_id(sid) == turn_id

    ok = assistant.clear_interactive_session(sid)
    assert ok is True

    drained = await turns.adrain_for_tests(sid, timeout=1)
    assert drained, (
        "B6 REGRESSION: assistant.clear_interactive_session (the function DELETE "
        "/api/chat/{id} calls) does not cancel a turn registered for the session it just "
        "deleted."
    )
    assert turns._turns.get(turn_id) is None
