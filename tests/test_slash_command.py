"""Unit and integration tests for the slash-command skill trigger.

Tests:
  * ``resolve_slash_skills`` — structured and stacked direct host requests
  * POST /api/chat with a ``/<slug>`` message — TestClient smoke for the direct request
  * Unknown slug falls through as plain text (no injection)

Hermetic: ``SkillStore`` is redirected to ``tmp_path``; ``assistant.run`` is replaced by a
spy that yields a ``turn_done`` event without contacting the LLM.
"""

from __future__ import annotations

from typing import AsyncGenerator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import backend.ai.assistant as assistant
import backend.ai.turns as turns
import backend.api.chat as chat_mod
from backend.core.errors import ValidationError
from backend.db import skill_store
from backend.db.skill_store import SkillStore
from backend.services.slash_command import resolve_slash_skills


@pytest.fixture
def skill_store_isolated(tmp_path, monkeypatch):
    # File-first layout: isolate via the skills folder + state file (no skills.json).
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    monkeypatch.setattr(skill_store, "SKILLS_DIR", skills_dir)
    monkeypatch.setattr(skill_store, "SKILLS_STATE_JSON", tmp_path / "skills_state.json")
    return tmp_path


# ── resolve_slash_skills: host-direct invocation ─────────────────────────────


def _create_direct_skill(store: SkillStore, name: str, **extra) -> dict:
    return store.create({"name": name, "description": name, **extra})


def test_direct_resolution_expands_one_skill_and_leaves_text_to_conversation(skill_store_isolated):
    store = SkillStore()
    _create_direct_skill(store, "monthly-close")

    resolution = resolve_slash_skills("/monthly-close 2026-05")

    assert resolution is not None
    assert list(resolution.skill_ids) == ["monthly-close"]
    assert resolution.argument_text == "2026-05"


def test_direct_resolution_honors_claude_user_invocable_and_refuses_disabled(
    skill_store_isolated,
):
    store = SkillStore()
    _create_direct_skill(store, "model-only", claude={"user_invocable": False})
    _create_direct_skill(store, "disabled", enabled=False)

    assert resolve_slash_skills("/model-only") is None
    assert resolve_slash_skills("/disabled") is None


def test_direct_resolution_stacks_skills_with_one_shared_trailing_argument(skill_store_isolated):
    store = SkillStore()
    _create_direct_skill(store, "first")
    _create_direct_skill(store, "second")

    resolution = resolve_slash_skills("/first /second Q1 report")

    assert resolution is not None
    assert list(resolution.skill_ids) == ["first", "second"]
    assert resolution.argument_text == "Q1 report"


def test_direct_resolution_dedupes_repeated_slash_skills(skill_store_isolated):
    store = SkillStore()
    _create_direct_skill(store, "same")

    resolution = resolve_slash_skills("/same /same do it")

    assert resolution is not None
    assert resolution.skill_ids == ("same",)
    assert resolution.argument_text == "do it"


def test_direct_resolution_matches_claude_six_skill_stack_limit(skill_store_isolated):
    store = SkillStore()
    for number in range(1, 8):
        _create_direct_skill(store, f"skill-{number}")

    resolution = resolve_slash_skills(
        "/skill-1 /skill-2 /skill-3 /skill-4 /skill-5 /skill-6 /skill-7 Q1"
    )

    assert resolution is not None
    assert list(resolution.skill_ids) == [
        "skill-1",
        "skill-2",
        "skill-3",
        "skill-4",
        "skill-5",
        "skill-6",
    ]
    assert resolution.argument_text == "/skill-7 Q1"


def test_direct_resolution_unknown_slash_remains_normal_text(skill_store_isolated):
    assert resolve_slash_skills("/not-a-skill explain this") is None


def test_direct_resolution_uses_structured_skill_identity_only(
    skill_store_isolated,
):
    store = SkillStore()
    _create_direct_skill(store, "monthly-close")

    resolution = resolve_slash_skills("2026-05", structured_skills=["monthly-close"])

    assert resolution is not None
    assert list(resolution.skill_ids) == ["monthly-close"]
    assert resolution.argument_text == "2026-05"


def test_structured_skill_identity_strips_its_display_token_from_arguments(
    skill_store_isolated,
):
    store = SkillStore()
    _create_direct_skill(store, "monthly-close")

    resolution = resolve_slash_skills(
        "[skill: monthly-close|Monthly close] Q2 notes",
        structured_skills=["monthly-close"],
    )

    assert resolution is not None
    assert list(resolution.skill_ids) == ["monthly-close"]
    assert resolution.argument_text == "Q2 notes"


def test_structured_skills_preserve_order_and_dedupe(
    skill_store_isolated,
):
    store = SkillStore()
    _create_direct_skill(store, "monthly-close")
    _create_direct_skill(store, "budget-review")

    resolution = resolve_slash_skills(
        "[skill: monthly-close|Monthly close] [skill: budget-review|Budget review] Q2 notes",
        structured_skills=["monthly-close", "budget-review", "monthly-close"],
    )

    assert resolution is not None
    assert list(resolution.skill_ids) == ["monthly-close", "budget-review"]
    assert resolution.argument_text == "Q2 notes"


def test_structured_skills_reject_a_seventh_unique_selection(skill_store_isolated):
    store = SkillStore()
    ids = [f"skill-{number}" for number in range(1, 8)]
    for skill_id in ids:
        _create_direct_skill(store, skill_id)

    with pytest.raises(ValidationError, match="At most 6"):
        resolve_slash_skills("run them", structured_skills=ids)


def test_structured_skill_strips_matching_leading_slash_marker(skill_store_isolated):
    store = SkillStore()
    _create_direct_skill(store, "monthly-close")

    resolution = resolve_slash_skills(
        "/monthly-close Q2 notes",
        structured_skills=["monthly-close"],
    )

    assert resolution is not None
    assert resolution.argument_text == "Q2 notes"


def test_direct_resolution_rejects_disabled_structured_skill_selection(skill_store_isolated):
    store = SkillStore()
    name = "disabled-structured"
    _create_direct_skill(store, name, enabled=False)

    with pytest.raises(ValidationError):
        resolve_slash_skills("2026-05", structured_skills=[name])


def test_direct_resolution_rejects_unknown_structured_skill_selection(skill_store_isolated):
    with pytest.raises(ValidationError):
        resolve_slash_skills("2026-05", structured_skills=["not-a-skill"])


# ── POST /api/chat — TestClient smoke ────────────────────────────────────────


@pytest.fixture
def chat_client(tmp_path, monkeypatch):
    # File-first layout: isolate via the skills folder + state file (no skills.json).
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    monkeypatch.setattr(skill_store, "SKILLS_DIR", skills_dir)
    monkeypatch.setattr(skill_store, "SKILLS_STATE_JSON", tmp_path / "skills_state.json")
    # Seed an enabled skill.
    s = SkillStore()
    s.create({"name": "budget-review", "description": "Review budget", "allowed_tools": []})

    # sessions is DB-backed now (Wave 5E, JSON->DB) and isolated per test by conftest's
    # autouse Tier-2 table clear — no per-store monkeypatch needed any more.
    monkeypatch.setattr("backend.ai.conversation.build_system_prompt", lambda **_kwargs: "SYS")

    captured: dict = {}

    async def fake_run(session_id, new_messages, system, **kw) -> AsyncGenerator[dict, None]:
        captured["new_messages"] = new_messages
        captured["direct_invocations"] = kw.get("direct_invocations") or []
        yield {"type": "turn_done"}

    monkeypatch.setattr(assistant, "run", fake_run)

    # Argument-shaping test, not the concurrent shape — see test_chat_mentions.py's
    # fixture for the same rationale.
    turns.set_inline_for_tests(True)
    app = FastAPI()
    app.include_router(chat_mod.router)
    yield TestClient(app), captured
    turns.set_inline_for_tests(False)


def _last_user_text(new_messages: list[dict]) -> str:
    content = new_messages[-1]["content"]
    if isinstance(content, str):
        return content
    return "\n".join(
        p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text"
    )


def test_slash_command_passes_a_structured_direct_invocation_without_a_text_directive(chat_client):
    c, captured = chat_client
    r = c.post(
        "/api/chat",
        json={
            "session_id": "s1",
            "messages": [{"role": "user", "content": "/budget-review"}],
        },
    )
    assert r.status_code == 202
    text = _last_user_text(captured["new_messages"])
    assert text == "/budget-review"
    requests = captured["direct_invocations"]
    assert len(requests) == 1
    assert requests[0].skill_id == "budget-review"
    assert requests[0].argument_text == ""


def test_slash_command_keeps_trailing_text_in_original_user_message(chat_client):
    c, captured = chat_client
    c.post(
        "/api/chat",
        json={
            "session_id": "s2",
            "messages": [{"role": "user", "content": "/budget-review Q1 notes"}],
        },
    )
    assert _last_user_text(captured["new_messages"]) == "/budget-review Q1 notes"
    assert captured["direct_invocations"][0].argument_text == "Q1 notes"


def test_unknown_slug_falls_through_as_plain_text(chat_client):
    c, captured = chat_client
    r = c.post(
        "/api/chat",
        json={
            "session_id": "s3",
            "messages": [{"role": "user", "content": "/unknown-xyz"}],
        },
    )
    assert r.status_code == 202
    text = _last_user_text(captured["new_messages"])
    # The plain text should be passed through unchanged (no injection).
    assert "SLASH COMMAND" not in text
    # The original message text must still be present.
    assert "/unknown-xyz" in text
    assert captured["direct_invocations"] == []


def test_non_slash_message_unaffected(chat_client):
    c, captured = chat_client
    r = c.post(
        "/api/chat",
        json={
            "session_id": "s4",
            "messages": [{"role": "user", "content": "what is my net worth"}],
        },
    )
    assert r.status_code == 202
    text = _last_user_text(captured["new_messages"])
    assert "SLASH COMMAND" not in text
    assert "what is my net worth" in text
    assert captured["direct_invocations"] == []


def test_structured_skill_keeps_visible_text_as_conversation_input(chat_client):
    c, captured = chat_client
    response = c.post(
        "/api/chat",
        json={
            "session_id": "s5",
            "messages": [{"role": "user", "content": "Q2 notes"}],
            "skills": ["budget-review"],
        },
    )

    assert response.status_code == 202
    request = captured["direct_invocations"][0]
    assert request.skill_id == "budget-review"
    assert request.argument_text == "Q2 notes"
    assert _last_user_text(captured["new_messages"]) == "Q2 notes"


def test_slash_skill_with_attachment_keeps_typed_trailing_text(chat_client):
    c, captured = chat_client
    response = c.post(
        "/api/chat",
        json={
            "session_id": "s6",
            "messages": [{"role": "user", "content": "/budget-review Q3 notes"}],
            "attachments": ["missing-ref"],
        },
    )

    assert response.status_code == 202
    request = captured["direct_invocations"][0]
    assert request.skill_id == "budget-review"
    assert "Q3 notes" in _last_user_text(captured["new_messages"])


def test_structured_skill_with_attachment_keeps_typed_text(chat_client):
    c, captured = chat_client
    response = c.post(
        "/api/chat",
        json={
            "session_id": "s7",
            "messages": [{"role": "user", "content": "Q4 notes"}],
            "attachments": ["missing-ref"],
            "skills": ["budget-review"],
        },
    )

    assert response.status_code == 202
    request = captured["direct_invocations"][0]
    assert request.skill_id == "budget-review"
    assert "Q4 notes" in _last_user_text(captured["new_messages"])


@pytest.mark.parametrize("skill_id", ["../budget-review", "budget/review", "UPPER", "bad_slug"])
def test_malformed_structured_skill_id_returns_422_not_500(chat_client, skill_id):
    client, _captured = chat_client

    response = client.post(
        "/api/chat",
        json={
            "session_id": "malformed-structured",
            "messages": [{"role": "user", "content": "run this"}],
            "skills": [skill_id],
        },
    )

    assert response.status_code == 422
    assert "canonical slug" in response.json()["detail"]


def test_unknown_structured_skill_id_returns_422_not_500(chat_client):
    client, _captured = chat_client

    response = client.post(
        "/api/chat",
        json={
            "session_id": "unknown-structured",
            "messages": [{"role": "user", "content": "run this"}],
            "skills": ["missing-skill"],
        },
    )

    assert response.status_code == 422
    assert "Unknown structured skill" in response.json()["detail"]
