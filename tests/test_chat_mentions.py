"""POST /api/chat mention-injection smoke test (S1).

Hermetic: ``assistant.run`` is replaced by a spy that captures the assembled ``new_messages``
and yields a turn_done event, so the test asserts the resolved-mention context block was merged
onto the LAST user message (not the system prompt) without any LLM / network.
"""

from __future__ import annotations

from typing import AsyncGenerator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import backend.ai.assistant as assistant
import backend.ai.turns as turns
import backend.api.chat as chat_mod
from backend.core import fx
from backend.db.profile_store import ProfileStore

_FX = {"USD": 1.0}


@pytest.fixture
def client(tmp_path, monkeypatch):
    # profile is DB-backed now (Wave 5, JSON->DB); sessions are DB-backed too (Wave 5E) —
    # no more PROFILE_JSON file / assistant._sessions_dir to point at tmp_path. Both stores
    # resolve against the shared default engine, which tests/conftest.py migrates once per
    # session and clears after every test.
    ProfileStore().set_raw({"base_currency": "USD"})
    fx.set_fx_rates_loader(lambda: dict(_FX))
    # This file is about request -> argument shaping (what assistant.run was CALLED
    # with), not the concurrent shape Phase 3 adds — inline mode makes POST /api/chat
    # await its spawned turn before returning, so `captured` is populated by the time
    # the response comes back, exactly like the pre-Phase-3 synchronous SSE stream was.
    turns.set_inline_for_tests(True)

    monkeypatch.setattr("backend.ai.conversation.build_system_prompt", lambda **_kwargs: "SYS")
    # Keep mention resolution offline: the brief/analyst context now reads live Yahoo analyst
    # data; stub the fetchers so these chat smoke tests never touch the network.
    monkeypatch.setattr("backend.services.prices.fetch_analyst", lambda *a, **k: {}, raising=False)
    monkeypatch.setattr(
        "backend.services.prices.fetch_analyst_ratings", lambda *a, **k: [], raising=False
    )

    captured: dict = {}

    async def fake_run(session_id, new_messages, system, **kw) -> AsyncGenerator[dict, None]:
        from backend.ai.tools import _skill_scope

        captured["new_messages"] = new_messages
        captured["system"] = system
        captured["model"] = kw.get("model")
        # Snapshot the interactive responder scope live in run()'s async context (the routing
        # seam sets it before iterating run) so the routing tests can assert the tool ceiling.
        rs = _skill_scope.offer_tool_names()
        captured["responder_scope"] = sorted(rs) if rs is not None else None
        yield {"type": "turn_done"}

    monkeypatch.setattr(assistant, "run", fake_run)

    app = FastAPI()
    app.include_router(chat_mod.router)
    yield TestClient(app), captured
    fx.set_fx_rates_loader(None)
    turns.set_inline_for_tests(False)


def _last_user_text(new_messages: list[dict]) -> str:
    content = new_messages[-1]["content"]
    if isinstance(content, str):
        return content
    return "\n".join(p.get("text", "") for p in content if isinstance(p, dict))


def test_mentions_injected_onto_user_turn(client):
    import re

    c, captured = client
    r = c.post(
        "/api/chat",
        json={
            "session_id": "s1",
            "messages": [{"role": "user", "content": "explain this"}],
            "mentions": [{"type": "net_worth", "ref": "", "label": "Net worth"}],
        },
    )
    assert r.status_code == 202
    text = _last_user_text(captured["new_messages"])
    # P0-C baseline: the mention block is now fenced with a per-turn nonce.
    # Guard header is inside the fence body.
    assert "Referenced items the user is asking about" in text
    assert "UNTRUSTED" in text
    assert "@Net worth:" in text
    assert "explain this" in text
    # The fence markers must be present (nonce-bearing, non-forgeable).
    assert re.search(r"«mention:[0-9a-f]{32}»", text)
    assert re.search(r"«/mention:[0-9a-f]{32}»", text)
    assert "Referenced items" not in captured["system"]


def _user_text_blocks(new_messages: list[dict]) -> list[str]:
    content = new_messages[-1]["content"]
    if isinstance(content, str):
        return [content]
    return [p["text"] for p in content if isinstance(p, dict) and p.get("type") == "text"]


def test_user_text_leads_the_turn_before_context_blocks(client):
    """Regression: the user's own typed message must be the FIRST text block of the turn —
    context (mention data, screen snapshot) is appended AFTER it, never prepended in front.

    A buried instruction (sandwiched under a large screen dump) is why a cheap model answered
    the screen instead of the request. The user's words lead."""
    c, captured = client
    r = c.post(
        "/api/chat",
        json={
            "session_id": "lead1",
            "messages": [
                {"role": "user", "content": "this is a transfer from compass to mekong bank"}
            ],
            "mentions": [{"type": "net_worth", "ref": "", "label": "Net worth"}],
        },
    )
    assert r.status_code == 202
    blocks = _user_text_blocks(captured["new_messages"])
    # The user's own words come first; the mention context follows.
    assert blocks[0] == "this is a transfer from compass to mekong bank"
    assert any("Referenced items the user is asking about" in b for b in blocks[1:])


def test_no_mentions_leaves_user_turn_unchanged(client):
    c, captured = client
    r = c.post(
        "/api/chat",
        json={"session_id": "s2", "messages": [{"role": "user", "content": "plain question"}]},
    )
    assert r.status_code == 202
    assert captured["new_messages"][-1]["content"] == "plain question"


def test_bad_mention_does_not_500(client):
    import re

    c, captured = client
    r = c.post(
        "/api/chat",
        json={
            "session_id": "s3",
            "messages": [{"role": "user", "content": "what about this"}],
            "mentions": [{"type": "transaction", "ref": "ghost", "label": "Ghost"}],
        },
    )
    assert r.status_code == 202
    text = _last_user_text(captured["new_messages"])
    # P0-C baseline: not-found errors also ride the fenced block.
    assert "@Ghost: (not found)" in text
    assert re.search(r"«mention:[0-9a-f]{32}»", text)


# ── session-level brief: an asset mention binds the conversation so the cached brief
#    rides EVERY later turn, not just the chip turn ────────────────────────────────


def _seed_brief(instrument_id: str) -> None:
    from backend.db.asset_brief_store import AssetBriefStore

    AssetBriefStore().upsert_brief(
        {
            "instrument_id": instrument_id,
            "news": [{"headline": "ONWAI Inc weighs asset sales"}],
            "key_issues": [{"question": "Will the takeover close?"}],
            "portfolio_impact": "Affects your ONWAI position.",
            "synthesized_at": "2026-06-14T00:00:00+00:00",
        }
    )


def test_session_brief_injected_on_followup_turn_without_mention(client):
    c, captured = client
    _seed_brief("inst-onwai")

    # Turn 1 carries the instrument mention → binds the session AND already injects the brief
    # inline, so the standalone "Cached brief" header is NOT duplicated this turn.
    r1 = c.post(
        "/api/chat",
        json={
            "session_id": "sb1",
            "messages": [{"role": "user", "content": "tell me about this"}],
            "mentions": [{"type": "instrument", "ref": "inst-onwai", "label": "ONWAI Inc"}],
        },
    )
    assert r1.status_code == 202
    assert "Cached brief for ONWAI Inc" not in _last_user_text(captured["new_messages"])

    # Turn 2 has NO mention — the bound brief is injected so the follow-up answers from it.
    r2 = c.post(
        "/api/chat",
        json={
            "session_id": "sb1",
            "messages": [{"role": "user", "content": "what's the latest?"}],
        },
    )
    assert r2.status_code == 202
    text = _last_user_text(captured["new_messages"])
    assert "Cached brief for ONWAI Inc" in text
    assert "ONWAI Inc weighs asset sales" in text
    assert "Affects your ONWAI position." in text
    assert "what's the latest?" in text


def test_unbound_conversation_gets_no_brief_block(client):
    c, captured = client
    r = c.post(
        "/api/chat",
        json={"session_id": "sb2", "messages": [{"role": "user", "content": "hello there"}]},
    )
    assert r.status_code == 202
    text = _last_user_text(captured["new_messages"])
    assert "Cached brief" not in text
    assert text == "hello there"


# ── Agent routing (P3 §7) — no-regression on the Lucius path + persona/scope swap ──
def test_no_responding_agent_is_byte_identical_lucius_path(client):
    """responding_agent omitted → the existing Lucius path: the stubbed Lucius system prompt
    is used verbatim and NO responder tool scope is set."""
    c, captured = client
    r = c.post(
        "/api/chat",
        json={"session_id": "r0", "messages": [{"role": "user", "content": "plain"}]},
    )
    assert r.status_code == 202
    assert captured["system"] == "SYS"  # the Lucius system prompt, untouched
    assert captured["responder_scope"] is None  # no narrowing — Lucius's full offer


def test_responding_agent_lucius_string_is_also_unchanged(client):
    c, captured = client
    r = c.post(
        "/api/chat",
        json={
            "session_id": "r0b",
            "messages": [{"role": "user", "content": "plain"}],
            "responding_agent": "lucius",
        },
    )
    assert r.status_code == 202
    assert captured["system"] == "SYS"
    assert captured["responder_scope"] is None


def test_unknown_responding_agent_falls_back_to_lucius(client):
    c, captured = client
    r = c.post(
        "/api/chat",
        json={
            "session_id": "r0c",
            "messages": [{"role": "user", "content": "plain"}],
            "responding_agent": "ghost-agent",
        },
    )
    assert r.status_code == 202
    assert captured["system"] == "SYS"
    assert captured["responder_scope"] is None
