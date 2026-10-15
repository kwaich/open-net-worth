"""Telegram model/status/cost commands + the /model picker callback."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import backend.connectors.telegram.handlers as h


def _update(chat_id=7, args_text=""):
    replies = []
    msg = SimpleNamespace(reply_text=lambda t, **k: replies.append((t, k)) or _await_none())
    return (
        SimpleNamespace(
            effective_chat=SimpleNamespace(id=chat_id),
            effective_user=SimpleNamespace(username="u", first_name="U"),
            message=msg,
        ),
        replies,
    )


async def _await_none():
    return None


def _ctx(args):
    return SimpleNamespace(args=args, bot=SimpleNamespace())


def test_bot_commands_include_model_status_cost():
    names = [c for c, _ in h.BOT_COMMANDS]
    for n in ("model", "status", "cost"):
        assert n in names


def test_model_set_by_name_persists_a_canonical_conversation_override(monkeypatch):
    monkeypatch.setattr(h, "_policy_allows_turn", lambda cid: True)
    monkeypatch.setattr(h.session_map, "get_or_create_session", lambda *a: "sess-7")
    persisted = {}

    class _Store:
        def public(self):
            return {"default_model": "claude_cli:sonnet"}

        def resolve_override(self, ref):
            provider, _, model = ref.partition(":")
            return {"provider_key": provider, "model": model}

    monkeypatch.setattr("backend.db.model_config_store.ModelConfigStore", _Store)
    monkeypatch.setattr(
        "backend.ai.assistant.upsert_session_meta",
        lambda session_id, **changes: persisted.update(session_id=session_id, **changes),
    )
    upd, replies = _update(args_text="openrouter:deepseek/deepseek-v4")
    asyncio.run(h.handle_model(upd, _ctx(["openrouter:deepseek/deepseek-v4"])))
    assert persisted == {
        "session_id": "sess-7",
        "model": "openrouter:deepseek/deepseek-v4",
        "fail_loud": True,
    }
    assert replies and "OpenRouter • deepseek/deepseek-v4" in replies[0][0]


def test_model_command_rejects_a_bare_ambiguous_model_name(monkeypatch):
    monkeypatch.setattr(h, "_policy_allows_turn", lambda cid: True)
    monkeypatch.setattr(h.session_map, "get_or_create_session", lambda *a: "sess-7")

    class _Store:
        def public(self):
            return {"default_model": "claude_cli:sonnet"}

        def resolve_override(self, ref):
            provider, _, model = ref.partition(":")
            return {"provider_key": provider, "model": model}

    monkeypatch.setattr("backend.db.model_config_store.ModelConfigStore", _Store)
    persisted = []
    monkeypatch.setattr(
        "backend.ai.assistant.upsert_session_meta",
        lambda *args, **kwargs: persisted.append((args, kwargs)),
    )

    upd, replies = _update(args_text="sonnet")
    asyncio.run(h.handle_model(upd, _ctx(["sonnet"])))

    assert persisted == []
    assert "provider:model" in replies[0][0]
    assert "Claude Code CLI • sonnet" in replies[0][0]


def test_model_picker_preserves_provider_identity_and_displays_it(monkeypatch):
    monkeypatch.setattr(h, "_policy_allows_turn", lambda cid: True)
    monkeypatch.setattr(h.session_map, "get_or_create_session", lambda *a: "sess-7")
    monkeypatch.setattr("backend.ai.assistant.session_model", lambda _sid: None)

    class _Store:
        def public(self):
            return {
                "default_model": "claude_cli:sonnet",
                "task_models": {"chat_vision": "openrouter:anthropic/sonnet"},
            }

        def resolve_override(self, ref):
            provider, _, model = ref.partition(":")
            return {"provider_key": provider, "model": model}

    monkeypatch.setattr("backend.db.model_config_store.ModelConfigStore", _Store)
    h._model_menu.clear()
    upd, replies = _update()
    asyncio.run(h.handle_model(upd, _ctx([])))
    menu_session_id, menu_choices = h._model_menu[7]
    assert menu_session_id == "sess-7"
    assert [choice.ref for choice in menu_choices] == [
        "claude_cli:sonnet",
        "openrouter:anthropic/sonnet",
    ]
    keyboard = replies[0][1]["reply_markup"].inline_keyboard
    labels = [row[0].text for row in keyboard]
    assert labels == ["● Claude Code CLI • sonnet", "OpenRouter • anthropic/sonnet"]
    assert "Current model: Claude Code CLI • sonnet" in replies[0][0]


def test_status_reports_this_conversations_provider_pinned_model(monkeypatch):
    monkeypatch.setattr(h, "_policy_allows_turn", lambda cid: True)
    monkeypatch.setattr(h.session_map, "get_or_create_session", lambda *a: "sess-7")
    monkeypatch.setattr(
        "backend.ai.assistant.session_model",
        lambda _session_id: "codex_cli:gpt-5.6-luna",
    )

    class _Store:
        def public(self):
            return {"default_model": "claude_cli:sonnet"}

        def resolve_override(self, ref):
            provider, _, model = ref.partition(":")
            return {"provider_key": provider, "model": model}

    monkeypatch.setattr(
        "backend.db.model_config_store.ModelConfigStore",
        _Store,
    )
    monkeypatch.setattr(
        "backend.db.telegram_config_store.TelegramConfigStore",
        lambda: SimpleNamespace(streaming_enabled=lambda: True),
    )
    monkeypatch.setattr(h.session_map, "get_effort", lambda c, s: "low")
    monkeypatch.setattr(
        "backend.services.cost.cost_summary", lambda **k: {"total": {"cost_usd": 0.12, "turns": 3}}
    )
    upd, replies = _update()
    asyncio.run(h.handle_status(upd, _ctx([])))
    out = replies[0][0]
    assert "gpt-5.6-luna" in out
    assert "Codex CLI" in out
    assert "low" in out and "on" in out and "$0.120" in out


def test_model_picker_callback_persists_the_canonical_conversation_ref(monkeypatch):
    monkeypatch.setattr(h, "_policy_allows_turn", lambda cid: True)
    monkeypatch.setattr(h.session_map, "get_or_create_session", lambda *a: "sess-7")
    h._model_menu[7] = (
        "sess-7",
        [
            h.ModelChoice(
                ref="openrouter:anthropic/sonnet",
                provider_key="openrouter",
                provider_label="OpenRouter",
                model_id="anthropic/sonnet",
            )
        ],
    )
    persisted = {}

    class _Store:
        def public(self):
            return {"default_model": "claude_cli:sonnet"}

        def resolve_override(self, ref):
            provider, _, model = ref.partition(":")
            return {"provider_key": provider, "model": model}

    monkeypatch.setattr("backend.db.model_config_store.ModelConfigStore", _Store)
    monkeypatch.setattr(
        "backend.ai.assistant.upsert_session_meta",
        lambda session_id, **changes: persisted.update(session_id=session_id, **changes),
    )
    edits = []
    query = SimpleNamespace(
        data="model:0",
        from_user=SimpleNamespace(id=7),
        answer=_await_none,
        edit_message_text=lambda text: edits.append(text) or _await_none(),
    )
    update = SimpleNamespace(
        callback_query=query,
        effective_chat=SimpleNamespace(id=7),
    )

    asyncio.run(h.handle_callback(update, _ctx([])))

    assert persisted == {
        "session_id": "sess-7",
        "model": "openrouter:anthropic/sonnet",
        "fail_loud": True,
    }
    assert edits == ["✅ Model set to: OpenRouter • anthropic/sonnet"]


def test_model_picker_callback_expires_after_the_conversation_changes(monkeypatch):
    monkeypatch.setattr(h, "_policy_allows_turn", lambda cid: True)
    monkeypatch.setattr(h.session_map, "get_or_create_session", lambda *a: "sess-new")
    h._model_menu[7] = (
        "sess-old",
        [
            h.ModelChoice(
                ref="claude_cli:sonnet",
                provider_key="claude_cli",
                provider_label="Claude Code CLI",
                model_id="sonnet",
            )
        ],
    )
    persisted = []
    monkeypatch.setattr(
        "backend.ai.assistant.upsert_session_meta",
        lambda *args, **kwargs: persisted.append((args, kwargs)),
    )
    edits = []
    query = SimpleNamespace(
        data="model:0",
        from_user=SimpleNamespace(id=7),
        answer=_await_none,
        edit_message_text=lambda text: edits.append(text) or _await_none(),
    )
    update = SimpleNamespace(
        callback_query=query,
        effective_chat=SimpleNamespace(id=7),
    )

    asyncio.run(h.handle_callback(update, _ctx([])))

    assert persisted == []
    assert edits == ["That model menu expired — send /model again."]


def test_model_picker_callback_refuses_a_provider_disabled_after_render(monkeypatch):
    from backend.core.errors import ProviderDisabledError

    monkeypatch.setattr(h, "_policy_allows_turn", lambda cid: True)
    monkeypatch.setattr(h.session_map, "get_or_create_session", lambda *a: "sess-7")
    h._model_menu[7] = (
        "sess-7",
        [
            h.ModelChoice(
                ref="xai:grok-4",
                provider_key="xai",
                provider_label="xAI",
                model_id="grok-4",
            )
        ],
    )

    class _Store:
        def resolve_override(self, _ref):
            raise ProviderDisabledError("xAI is disabled", provider_key="xai", label="xAI")

    monkeypatch.setattr("backend.db.model_config_store.ModelConfigStore", _Store)
    persisted = []
    monkeypatch.setattr(
        "backend.ai.assistant.upsert_session_meta",
        lambda *args, **kwargs: persisted.append((args, kwargs)),
    )
    edits = []
    query = SimpleNamespace(
        data="model:0",
        from_user=SimpleNamespace(id=7),
        answer=_await_none,
        edit_message_text=lambda text: edits.append(text) or _await_none(),
    )
    update = SimpleNamespace(callback_query=query, effective_chat=SimpleNamespace(id=7))

    asyncio.run(h.handle_callback(update, _ctx([])))

    assert persisted == []
    assert edits == ["Can't switch model: xAI is disabled"]
