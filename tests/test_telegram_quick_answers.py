"""Quick-answer inline buttons for bare commands + preferred models in the /model picker."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import backend.connectors.telegram.handlers as h
from backend.services.model_selection import configured_model_choices


def _update(chat_id=7):
    replies = []
    msg = SimpleNamespace(reply_text=lambda t, **kw: replies.append((t, kw)) or _none())
    return (
        SimpleNamespace(
            effective_chat=SimpleNamespace(id=chat_id),
            effective_user=SimpleNamespace(username="u", first_name="U"),
            message=msg,
        ),
        replies,
    )


async def _none():
    return None


def test_bare_effort_shows_option_buttons(monkeypatch):
    monkeypatch.setattr(h, "_policy_allows_turn", lambda c: True)
    monkeypatch.setattr(h.session_map, "get_effort", lambda c, s: "low")
    upd, replies = _update()
    asyncio.run(h.handle_effort(upd, SimpleNamespace(args=[])))
    text, kw = replies[0]
    kb = kw["reply_markup"].inline_keyboard[0]
    labels = [b.text for b in kb]
    assert any("low" in x for x in labels) and any("max" in x for x in labels)
    assert kb[0].callback_data.startswith("set:effort:")


def test_choice_keyboard_marks_current():
    kb = h._choice_keyboard("streaming", ["on", "off"], current="on")
    row = kb.inline_keyboard[0]
    assert row[0].text == "● on" and row[0].callback_data == "set:streaming:on"
    assert row[1].text == "off"


def test_model_choices_include_preferred(monkeypatch):
    class _Store:
        def public(self):
            return {"default_model": "glm:glm-5.2", "task_models": {}}

        def resolve_override(self, ref):
            provider, _, model = ref.partition(":")
            return {"provider_key": provider, "model": model}

    choices = configured_model_choices(
        store=_Store(),
        preferred_refs=["openrouter:anthropic/claude-opus", "xai:grok-4"],
    )
    assert [choice.ref for choice in choices] == [
        "glm:glm-5.2",
        "openrouter:anthropic/claude-opus",
        "xai:grok-4",
    ]
    assert [choice.display for choice in choices] == [
        "Zhipu (GLM) • glm-5.2",
        "OpenRouter • anthropic/claude-opus",
        "xAI (Grok) • grok-4",
    ]
