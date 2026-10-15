"""Per-conversation model picker — POST /api/chat honors + persists a per-session model.

Hermetic: the session index lives in a tmp dir; ``assistant.run`` is monkeypatched to
capture the ``model`` kwarg (no LLM, no network). Proves the resolution order:
explicit request model → session-stored model → None (instance default), and that an
explicit choice is persisted onto the session so it sticks for the next turn.
"""

from __future__ import annotations

from typing import Optional

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import backend.ai.assistant as assistant
import backend.ai.conversation as conversation
import backend.ai.turns as turns
import backend.api.chat as chat_mod
from backend.db.model_config_store import ModelConfigStore


@pytest.fixture
def client(tmp_path, monkeypatch):
    # sessions is DB-backed now (Wave 5E, JSON->DB) and isolated per test by conftest's
    # autouse Tier-2 table clear — no per-store monkeypatch needed any more.
    captured: dict = {}

    # Don't build the real (heavy) system prompt, but retain the model context the
    # catalog was rendered against.
    def fake_system_prompt(*, model=None, base_url=None):
        captured["prompt_model"] = model
        captured["prompt_base_url"] = base_url
        after_prompt = captured.get("after_prompt")
        if after_prompt is not None:
            after_prompt()
        return "SYS"

    monkeypatch.setattr(conversation, "build_system_prompt", fake_system_prompt)

    def fake_resolve_override(self, override):
        provider_key, separator, bare_model = override.partition(":")
        if separator:
            return {
                "provider_key": provider_key,
                "transport": "openai_compatible",
                "base_url": f"https://{provider_key}.example/v1",
                "api_key": "",
                "model": bare_model,
            }
        return {
            "provider_key": None,
            "transport": "openai_compatible",
            "base_url": "https://default.example/v1",
            "api_key": "",
            "model": override,
        }

    monkeypatch.setattr(ModelConfigStore, "resolve_override", fake_resolve_override)
    monkeypatch.setattr(
        ModelConfigStore,
        "resolved",
        lambda self: {
            "provider": "openai_compatible",
            "base_url": "https://default.example/v1",
            "api_key": "",
            "model": "default-model",
        },
    )
    monkeypatch.setattr(
        ModelConfigStore,
        "public",
        lambda self: {"default_model": "openai:default-model"},
    )

    async def fake_run(session_id, new_messages, system, model=None, effort="medium", **kw):
        captured["model"] = model
        captured["model_context"] = kw.get("model_context")
        captured["session_id"] = session_id
        yield {"type": "turn_done"}

    monkeypatch.setattr(assistant, "run", fake_run)
    # Argument-shaping test, not the concurrent shape — see test_chat_mentions.py.
    monkeypatch.setattr(turns, "_inline", True)

    app = FastAPI()
    app.include_router(chat_mod.router)
    c = TestClient(app)
    c.captured = captured  # type: ignore[attr-defined]
    return c


def _post(c: TestClient, session_id: str, model: Optional[str]) -> None:
    body = {"session_id": session_id, "messages": [{"role": "user", "content": "hi"}]}
    if model is not None:
        body["model"] = model
    r = c.post("/api/chat", json=body)
    assert r.status_code == 202


def test_explicit_model_is_passed_and_persisted(client):
    _post(client, "sess-1", "claude-opus-4-6")
    # Honored this turn …
    assert client.captured["model"] == "openai:claude-opus-4-6"  # type: ignore[attr-defined]
    # … and persisted onto the session so it sticks.
    assert assistant.session_model("sess-1") == "openai:claude-opus-4-6"


def test_session_model_used_when_request_omits_it(client):
    # First turn sets the override.
    _post(client, "sess-2", "gpt-4o-mini")
    assert assistant.session_model("sess-2") == "openai:gpt-4o-mini"
    # A later turn with no model in the body inherits the stored one.
    _post(client, "sess-2", None)
    assert client.captured["model"] == "openai:gpt-4o-mini"  # type: ignore[attr-defined]


def test_blank_model_clears_override_and_falls_back_to_default(client):
    _post(client, "sess-3", "claude-opus-4-6")
    assert assistant.session_model("sess-3") == "openai:claude-opus-4-6"
    # An explicit blank selection ("Default") clears the stored override, while this
    # turn snapshots the provider-pinned instance default.
    _post(client, "sess-3", "")
    assert client.captured["model"] == "openai:default-model"  # type: ignore[attr-defined]
    assert assistant.session_model("sess-3") is None


def test_blank_model_on_automation_conversation_uses_instance_default(client, monkeypatch):
    monkeypatch.setattr(
        "backend.services.automations.runs.automation_run_for_session",
        lambda _session_id: (
            {"model": "openai:automation-current"},
            {"model": "openai:automation-run"},
        ),
    )

    async def fake_followup(_session_id, _messages, *, model=None, **_kwargs):
        client.captured["model"] = model  # type: ignore[attr-defined]
        yield {"type": "turn_done"}

    monkeypatch.setattr(
        "backend.ai.automation_runner_adapter.stream_automation_followup",
        fake_followup,
    )

    _post(client, "automation-session-default", "")

    assert client.captured["model"] == "openai:default-model"  # type: ignore[attr-defined]


def test_automation_conversation_receives_direct_skill_invocations(client, monkeypatch, tmp_path):
    from backend.db import skill_store
    from backend.db.skill_store import SkillStore

    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    monkeypatch.setattr(skill_store, "SKILLS_DIR", skills_dir)
    monkeypatch.setattr(skill_store, "SKILLS_STATE_JSON", tmp_path / "skills-state.json")
    SkillStore().create(
        {"name": "automation-skill", "description": "Use for Automation follow-ups."}
    )
    monkeypatch.setattr(
        "backend.services.automations.runs.automation_run_for_session",
        lambda _session_id: ({}, {}),
    )

    async def fake_followup(_session_id, _messages, **kwargs):
        client.captured["direct_invocations"] = kwargs.get("direct_invocations")  # type: ignore[attr-defined]
        yield {"type": "turn_done"}

    monkeypatch.setattr(
        "backend.ai.automation_runner_adapter.stream_automation_followup",
        fake_followup,
    )

    response = client.post(
        "/api/chat",
        json={
            "session_id": "automation-skill-session",
            "messages": [{"role": "user", "content": "/automation-skill reconcile Q2"}],
        },
    )

    assert response.status_code == 202
    requests = client.captured["direct_invocations"]  # type: ignore[attr-defined]
    assert len(requests) == 1
    assert requests[0].skill_id == "automation-skill"
    assert requests[0].argument_text == "reconcile Q2"


def test_no_override_means_instance_default(client):
    _post(client, "sess-4", None)
    # No request/stored override → one provider-pinned snapshot of the instance default.
    assert client.captured["model"] == "openai:default-model"  # type: ignore[attr-defined]


def test_unknown_provider_ref_is_rejected_as_request_validation(client):
    response = client.post(
        "/api/chat",
        json={
            "session_id": "sess-unknown-provider",
            "messages": [{"role": "user", "content": "hi"}],
            "model": "not_a_provider:sonnet",
        },
    )

    assert response.status_code == 422
    assert "Unknown AI provider" in response.json()["detail"]
    assert assistant.session_model("sess-unknown-provider") is None


def test_selected_model_context_drives_both_catalog_and_provider(client):
    _post(client, "sess-selected", "openai:gpt-selected")

    assert client.captured["prompt_model"] == "gpt-selected"  # type: ignore[attr-defined]
    assert client.captured["prompt_base_url"] == "https://openai.example/v1"  # type: ignore[attr-defined]
    # Keep the provider-qualified override intact: assistant.run resolves this exact
    # value to the same provider/base URL context used to build the catalog above.
    assert client.captured["model"] == "openai:gpt-selected"  # type: ignore[attr-defined]


def test_provider_uses_snapshot_when_config_changes_after_prompt(client, monkeypatch):
    changed = {"value": False}

    def resolve_override(self, override):
        _, _, bare_model = override.partition(":")
        return {
            "provider_key": "openai",
            "transport": "openai_compatible",
            "base_url": (
                "https://changed.example/v1" if changed["value"] else "https://selected.example/v1"
            ),
            "api_key": "changed-key" if changed["value"] else "selected-key",
            "model": "changed-model" if changed["value"] else bare_model,
        }

    monkeypatch.setattr(ModelConfigStore, "resolve_override", resolve_override)
    client.captured["after_prompt"] = lambda: changed.update(value=True)  # type: ignore[attr-defined]

    _post(client, "sess-snapshot", "openai:gpt-selected")

    context = client.captured["model_context"]  # type: ignore[attr-defined]
    assert context is not None
    assert context.model == "gpt-selected"
    assert context.base_url == "https://selected.example/v1"
    assert context.provider.base_url == "https://selected.example/v1"
