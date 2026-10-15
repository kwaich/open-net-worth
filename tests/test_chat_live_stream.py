"""Phase 3 of the concurrent-conversations RFC: ``POST /api/chat`` and
``POST /api/chat/{id}/resolve`` stop driving turns inside the request. Each validates,
spawns the turn as a supervised background task (``backend.ai.turns``), and returns
``202 {session_id, turn_id, accepted}``; the client attaches via
``GET /api/sessions/{id}/live``, the same endpoint an Automation run has always used.
``POST /api/chat/{id}/stop`` cancels a turn by session id (or a specific ``turn_id``), and
``GET /api/sessions`` surfaces how the last turn ended (``last_turn_status`` /
``last_turn_at``). Security-review additions (2026-08-06) are covered alongside: no
wildcard CORS header on any streaming response, a CSRF guard + an Automation-owned refusal
on ``/stop``.

Hermetic: no network, no LLM. ``assistant.run`` is a scripted fake async generator
(matching ``tests/test_chat_friendly_errors.py``'s pattern) for most tests; two tests drive
``backend.api.chat.chat`` directly (real ``asyncio`` concurrency, ``turns.set_inline_for_
tests`` OFF) to get real interleaving control over a paused mid-turn — the same technique
``tests/test_p0_5_concurrency.py`` uses on ``assistant.run`` itself.

``DATA_DIR`` is globally redirected to a throwaway tree by ``tests/conftest.py``; sessions and
pending plans are DB-backed (Wave 5E, JSON->DB) and isolated per test by conftest's autouse
Tier-2 table clear — no per-store monkeypatch needed any more.
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
from backend.db.pending_plan_store import PendingPlanStore
from tests._asgi import make_request


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
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


def _parse_events(text: str) -> list[dict]:
    events = []
    for line in text.splitlines():
        if not line.startswith("data: "):
            continue
        payload = line[6:].strip()
        if payload == "[DONE]":
            break
        try:
            events.append(json.loads(payload))
        except json.JSONDecodeError:
            pass
    return events


def _live_events(client: TestClient, session_id: str) -> list[dict]:
    """Read back whatever a turn published, via the same attach path a real client uses.

    Deterministic without a sleep: the post-end grace window in ``live_sessions`` retains
    the replay buffer, so this works whether the turn is still running or already finished.
    """
    return _parse_events(client.get(f"/api/sessions/{session_id}/live").text)


def _fake_run(events_to_yield: list[dict], *, raise_after: Exception | None = None):
    async def fake_run(session_id, new_messages, system, model=None, effort="medium", **kw):
        for ev in events_to_yield:
            yield dict(ev)
        if raise_after is not None:
            raise raise_after

    return fake_run


def _post(c: TestClient, session_id: str) -> dict:
    r = c.post(
        "/api/chat",
        json={"session_id": session_id, "messages": [{"role": "user", "content": "hi"}]},
    )
    assert r.status_code == 202
    return r.json()


# ── the 202 contract ─────────────────────────────────────────────────────────────────────


def test_post_returns_202_with_session_and_turn_id(monkeypatch):
    monkeypatch.setattr(assistant, "run", _fake_run([{"type": "turn_done", "usage": {}}]))
    turns.set_inline_for_tests(True)
    c = _client()
    body = _post(c, "s-202")
    assert body["session_id"] == "s-202"
    assert body["turn_id"].startswith("turn_")
    assert body["accepted"] == "started"


# ── live_sessions wiring: spawn opens the episode, _drive closes it ─────────────────────


def test_a_completed_turn_is_no_longer_reported_live(monkeypatch):
    monkeypatch.setattr(
        assistant,
        "run",
        _fake_run([{"type": "text_delta", "text": "hi"}, {"type": "turn_done", "usage": {}}]),
    )
    turns.set_inline_for_tests(True)
    c = _client()
    _post(c, "s-done")
    assert live_sessions.live_session_ids() == []


def test_live_sessions_ends_on_the_error_path(monkeypatch):
    monkeypatch.setattr(
        assistant,
        "run",
        _fake_run([{"type": "text_delta", "text": "hi"}], raise_after=RuntimeError("boom")),
    )
    turns.set_inline_for_tests(True)
    c = _client()
    _post(c, "s-err")
    assert not live_sessions.is_live("s-err"), "an errored turn leaked its live session"


async def test_a_second_client_attaches_mid_turn_and_receives_replay(monkeypatch):
    """The turn is spawned (not inline) — spawn() opens the episode BEFORE the 202 is even
    built, so a watcher attaching right after reading the 202 can never find the session
    not-live (Phase 3's whole race-freedom argument)."""
    reached = asyncio.Event()
    proceed = asyncio.Event()

    async def fake_run(session_id, new_messages, system, model=None, effort="medium", **kw):
        yield {"type": "text_delta", "text": "already "}
        reached.set()
        await proceed.wait()
        yield {"type": "text_delta", "text": "said"}
        yield {"type": "turn_done", "usage": {}}

    monkeypatch.setattr(assistant, "run", fake_run)

    req = ChatRequest(session_id="s-mid", messages=[{"role": "user", "content": "hi"}])
    response = await chat_mod.chat(req, make_request())
    assert response.status_code == 202
    turn_id = json.loads(bytes(response.body))["turn_id"]

    await asyncio.wait_for(reached.wait(), timeout=1)

    # GET /api/sessions/live reports the interactive turn (not just Automation runs).
    assert live_sessions.live_session_ids() == ["s-mid"]
    assert chat_mod.list_live_sessions(make_request())["session_ids"] == ["s-mid"]

    # A second client attaches mid-turn and gets the replay-so-far immediately.
    watched: list[dict] = []

    async def _watch():
        async for ev in live_sessions.watch("s-mid"):
            watched.append(ev)

    watch_task = asyncio.ensure_future(_watch())
    await asyncio.sleep(0)
    replayed_texts = [e.get("text") for e in watched if e.get("type") == "text_delta"]
    assert replayed_texts == ["already "]
    assert any(e.get("type") == "turn_started" and e.get("turn_id") == turn_id for e in watched)

    proceed.set()
    await asyncio.wait_for(watch_task, timeout=1)

    watched_texts = [e.get("text") for e in watched if e.get("type") == "text_delta"]
    assert watched_texts == ["already ", "said"], "the watcher saw the SAME two deltas, in order"
    assert live_sessions.live_session_ids() == []
    assert turns.drain_for_tests("s-mid", timeout=1)


async def test_stop_ends_the_live_session_no_leak(monkeypatch):
    """turns.cancel (POST /stop) delivers the SAME asyncio.CancelledError a client
    disconnect used to; live_sessions must not leak the episode."""
    reached = asyncio.Event()

    async def fake_run(session_id, new_messages, system, model=None, effort="medium", **kw):
        yield {"type": "text_delta", "text": "hi"}
        reached.set()
        await asyncio.Event().wait()  # hangs — only a cancel ends this

    monkeypatch.setattr(assistant, "run", fake_run)

    req = ChatRequest(session_id="s-disconnect", messages=[{"role": "user", "content": "hi"}])
    response = await chat_mod.chat(req, make_request())
    assert response.status_code == 202

    await asyncio.wait_for(reached.wait(), timeout=1)
    assert live_sessions.is_live("s-disconnect")

    result = turns.cancel("s-disconnect")
    assert result["stopped"] is True
    assert await turns.adrain_for_tests("s-disconnect", timeout=1)

    assert not live_sessions.is_live("s-disconnect"), "a cancelled turn leaked its live session"


# ── session_id / turn_id passthrough (assistant.run stamps them; chat.py must not strip) ──


def test_events_from_the_endpoint_keep_their_session_id_and_turn_id(monkeypatch):
    async def fake_run(
        session_id, new_messages, system, model=None, effort="medium", turn_id=None, **kw
    ):
        # A real assistant.run() mints its OWN turn_id unless one is passed in — chat.py's
        # closure MUST thread turns.spawn's minted id through (turn_id=turn_id) rather than
        # let a second, disagreeing id be minted here. Asserting on it catches the "two id
        # namespaces for one turn" bug the RFC calls out explicitly.
        assert turn_id, "chat.py did not pass its spawned turn_id into assistant.run"
        yield {"type": "text_delta", "text": "hi", "session_id": session_id, "turn_id": turn_id}
        yield {"type": "turn_done", "usage": {}, "session_id": session_id, "turn_id": turn_id}

    monkeypatch.setattr(assistant, "run", fake_run)
    turns.set_inline_for_tests(True)
    c = _client()
    body = _post(c, "s-keep")
    events = _live_events(c, "s-keep")
    text_and_done = [e for e in events if e.get("type") in ("text_delta", "turn_done")]
    assert text_and_done, "expected at least one event"
    # The id on every event matches the one minted by spawn() and returned in the 202 body
    # — a client can never see a wire event whose turn_id disagrees with what it was told.
    assert all(
        e["session_id"] == "s-keep" and e["turn_id"] == body["turn_id"] for e in text_and_done
    )


# ── POST /api/chat/{id}/stop ─────────────────────────────────────────────────────────────


def test_stop_is_a_truthful_noop_when_nothing_is_running():
    c = _client()
    r = c.post("/api/chat/s-nothing/stop", json={}, headers={"X-Requested-With": "XMLHttpRequest"})
    assert r.status_code == 200
    assert r.json() == {"ok": True, "session_id": "s-nothing", "stopped": False}


async def test_stop_cancels_a_running_turn(monkeypatch):
    reached = asyncio.Event()

    async def fake_run(session_id, new_messages, system, model=None, effort="medium", **kw):
        yield {"type": "text_delta", "text": "hi"}
        reached.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(assistant, "run", fake_run)

    req = ChatRequest(session_id="s-stop", messages=[{"role": "user", "content": "hi"}])
    response = await chat_mod.chat(req, make_request())
    assert response.status_code == 202
    turn_id = json.loads(bytes(response.body))["turn_id"]

    await asyncio.wait_for(reached.wait(), timeout=1)

    c = _client()
    r = c.post("/api/chat/s-stop/stop", json={}, headers={"X-Requested-With": "XMLHttpRequest"})
    assert r.status_code == 200
    assert r.json() == {"ok": True, "session_id": "s-stop", "stopped": True}

    assert await turns.adrain_for_tests("s-stop", timeout=1)
    assert not live_sessions.is_live("s-stop")
    assert turns._turns.get(turn_id) is None


def test_stop_requires_the_csrf_header():
    c = _client()
    r = c.post("/api/chat/s-csrf/stop", json={})
    assert r.status_code == 403


def test_stop_no_longer_refuses_an_automation_owned_session(monkeypatch):
    """Inverted on 2026-08-06 (ADR ``one-turn-lifecycle-for-chat-and-automations``).

    This used to 409, on the reasoning that cancelling a run would leave its row stuck
    reporting ``running`` — and no other surface offered a Stop either, so a scheduled run
    that looped or burned money could not be interrupted anywhere in the product. Runs now
    register in ``turns`` like any chat turn, so one cancel path serves both and
    ``runs._task_done`` settles the row to ``cancelled``.
    """
    import backend.services.automations.runs as runs_mod

    monkeypatch.setattr(
        runs_mod,
        "automation_run_for_session",
        lambda sid: ({"id": "auto1"}, {"id": "run1", "status": "running"}),
    )
    c = _client()
    r = c.post(
        "/api/chat/automation-run-1/stop", json={}, headers={"X-Requested-With": "XMLHttpRequest"}
    )
    assert r.status_code == 200


def test_stop_stamps_cancelled_only_when_it_actually_stopped_something(monkeypatch):
    c = _client()
    r = c.post(
        "/api/chat/s-nothing-to-cancel/stop",
        json={},
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    assert r.status_code == 200
    assert r.json()["stopped"] is False
    rows = {row["id"]: row for row in assistant.list_sessions()}
    assert "s-nothing-to-cancel" not in rows, "a no-op stop must not fabricate a session row"


def test_stop_accepts_a_specific_turn_id_and_ignores_a_stale_one(monkeypatch):
    monkeypatch.setattr(assistant, "run", _fake_run([{"type": "turn_done", "usage": {}}]))
    turns.set_inline_for_tests(True)
    c = _client()
    body = _post(c, "s-turn-scoped")

    # The turn already completed (inline mode) — its id is now stale.
    r = c.post(
        "/api/chat/s-turn-scoped/stop",
        json={"turn_id": body["turn_id"]},
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    assert r.status_code == 200
    assert r.json()["stopped"] is False


# ── CORS: no wildcard on any streaming/live response (security review, 2026-08-06) ──────


@pytest.mark.parametrize(
    "make_request",
    [
        lambda c: c.post(
            "/api/chat",
            json={"session_id": "s-cors-a", "messages": [{"role": "user", "content": "hi"}]},
        ),
        lambda c: c.get("/api/sessions/s-cors-b/live"),
    ],
)
def test_streaming_endpoints_never_set_a_wildcard_cors_header(monkeypatch, make_request):
    monkeypatch.setattr(assistant, "run", _fake_run([{"type": "turn_done", "usage": {}}]))
    turns.set_inline_for_tests(True)
    c = _client()
    r = make_request(c)
    assert r.status_code in (200, 202)
    assert "access-control-allow-origin" not in {k.lower() for k in r.headers}


# ── last_turn_status / last_turn_at surfacing (acceptance E3 / E6) ──────────────────────


def _session_row(session_id: str) -> dict:
    rows = {row["id"]: row for row in assistant.list_sessions()}
    assert session_id in rows, f"{session_id} not in GET /api/sessions"
    return rows[session_id]


def test_last_turn_status_ok_is_stamped_deliberately_and_flows_through_list_sessions(monkeypatch):
    monkeypatch.setattr(
        assistant,
        "run",
        _fake_run([{"type": "text_delta", "text": "hi"}, {"type": "turn_done", "usage": {}}]),
    )
    turns.set_inline_for_tests(True)
    c = _client()
    _post(c, "s-ok")

    row = _session_row("s-ok")
    assert row["last_turn_status"] == "ok"
    assert row.get("last_turn_at")


def test_last_turn_status_error_on_the_exception_path(monkeypatch):
    monkeypatch.setattr(assistant, "run", _fake_run([], raise_after=RuntimeError("boom")))
    turns.set_inline_for_tests(True)
    c = _client()
    _post(c, "s-error-status")

    row = _session_row("s-error-status")
    assert row["last_turn_status"] == "error"
    assert row.get("last_turn_at")


async def test_last_turn_status_cancelled_is_stamped_only_by_stop(monkeypatch):
    reached = asyncio.Event()

    async def fake_run(session_id, new_messages, system, model=None, effort="medium", **kw):
        yield {"type": "text_delta", "text": "hi"}
        reached.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(assistant, "run", fake_run)

    c = _client()
    # Nothing running yet — the stamp path below is unreachable.
    r = c.post(
        "/api/chat/s-cancelled-status/stop",
        json={},
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    assert r.json()["stopped"] is False

    req = ChatRequest(session_id="s-cancelled-status", messages=[{"role": "user", "content": "hi"}])
    await chat_mod.chat(req, make_request())
    await asyncio.wait_for(reached.wait(), timeout=1)

    r2 = c.post(
        "/api/chat/s-cancelled-status/stop",
        json={},
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    assert r2.json()["stopped"] is True
    assert await turns.adrain_for_tests("s-cancelled-status", timeout=1)

    row = _session_row("s-cancelled-status")
    assert row["last_turn_status"] == "cancelled"


def test_last_turn_status_awaiting_approval_when_a_card_is_still_pending(monkeypatch):
    monkeypatch.setattr(
        assistant,
        "run",
        _fake_run(
            [
                {
                    "type": "proposed_change",
                    "tool_use_id": "t0",
                    "change": {"type": "add_transaction"},
                },
                {"type": "turn_done", "usage": {}},
            ]
        ),
    )
    PendingPlanStore().stage_pending("t0", "add_transactions", {}, session_id="s-pending")
    turns.set_inline_for_tests(True)

    c = _client()
    _post(c, "s-pending")

    row = _session_row("s-pending")
    assert row["last_turn_status"] == "awaiting_approval"
