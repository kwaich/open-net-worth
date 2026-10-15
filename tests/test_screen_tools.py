"""A screen loads its own tools (ADR 2026-09-26-a-screen-loads-its-own-tools).

On 2026-09-26 Lucius, asked on Plan → Budget to set caps from the Zero-Based targets, never had a
budget tool in its offer (CORE + find_tools-loaded only) and answered from ``get_profile`` alone,
pairing target amounts with envelope names by guesswork. A turn sent from a screen now loads that
screen's tools into the session, like a ``find_tools`` load."""

from __future__ import annotations

import uuid

import pytest

from backend.ai import assistant
from backend.ai.tools import TOOL_SCHEMAS
from backend.ai.tools._extra_groups_scope import set_extra_names
from backend.ai.tools._screen_tools import SCREEN_TOOLS, tools_for_screen

BUDGET_SCREEN = "Plan — Budget"


@pytest.fixture(autouse=True)
def _reset_loaded_names():
    set_extra_names(frozenset())
    yield
    set_extra_names(frozenset())


def _loaded(sid: str) -> set[str]:
    return set((assistant._session_meta_entry(sid) or {}).get("extra_tool_names") or [])


def test_every_screen_tool_is_a_registered_tool():
    registered = {t["name"] for t in TOOL_SCHEMAS}
    for screen, names in SCREEN_TOOLS.items():
        assert names <= registered, (screen, sorted(names - registered))


def test_the_budget_screen_brings_the_envelope_and_cap_tools():
    names = tools_for_screen(BUDGET_SCREEN)
    assert {"list_envelopes", "get_budget_view", "apply_budget_operation"} <= names
    assert tools_for_screen("Plan — Outlook") == frozenset()
    assert tools_for_screen(None) == frozenset()


def test_loading_is_additive_sticky_and_idempotent():
    sid = f"screen-tools-{uuid.uuid4().hex[:8]}"
    assistant.upsert_session_meta(sid, title="t", extra_tool_names=["get_report"])
    new = assistant.load_screen_tools(sid, BUDGET_SCREEN)
    assert new == tools_for_screen(BUDGET_SCREEN)
    assert _loaded(sid) == {"get_report"} | set(new)  # what find_tools loaded survives
    assert assistant.load_screen_tools(sid, BUDGET_SCREEN) == frozenset()
    assert assistant.load_screen_tools(sid, "Plan — Outlook") == frozenset()
    assert _loaded(sid) == {"get_report"} | set(new)  # another screen never unloads
    assistant.clear_session(sid)


def test_a_chat_turn_from_the_budget_screen_offers_its_tools(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import backend.ai.turns as turns
    import backend.api.chat as chat_mod

    monkeypatch.setattr("backend.ai.conversation.build_system_prompt", lambda **_kwargs: "SYS")

    async def fake_run(session_id, new_messages, system, **kw):
        yield {"type": "turn_done"}

    monkeypatch.setattr(assistant, "run", fake_run)
    monkeypatch.setattr(turns, "_inline", True)
    app = FastAPI()
    app.include_router(chat_mod.router)
    client = TestClient(app)

    sid = f"screen-tools-chat-{uuid.uuid4().hex[:8]}"
    r = client.post(
        "/api/chat",
        json={
            "session_id": sid,
            "messages": [{"role": "user", "content": "set caps from my targets"}],
            "screen": {"id": "lucius-dock", "title": BUDGET_SCREEN},
        },
    )
    assert r.status_code == 202
    assert tools_for_screen(BUDGET_SCREEN) <= _loaded(sid)
    # The next turn's offer is seeded from exactly these persisted names.
    set_extra_names(frozenset(_loaded(sid)))
    offered = {t["name"] for t in assistant._build_core_offer()}
    assert {"list_envelopes", "apply_budget_operation", "get_budget_view"} <= offered
    assistant.clear_session(sid)
