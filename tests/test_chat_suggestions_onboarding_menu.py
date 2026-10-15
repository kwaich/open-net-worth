"""The onboarding skill's first "what would you like to do first?" menu gets FIXED chips, with no
model call (persona QA round 9: the message said "Pick one below" and the chips under it were
grey skeleton bars, so nothing was clickable).

A later option question inside onboarding, or the same sentence in any other conversation, still
gets generated answers.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.ai import chat_suggestions as ai_cs
from backend.api import chat_suggestions as cs

MENU = (
    "Lovely to meet you, Mai. What would you like to do first? You can see where your money "
    "goes (spending), see everything you have in one place (net worth), set up a budget, "
    "check whether your money will last (retirement), or keep an eye on your investments. "
    "Pick one below, or tell me in your own words."
)
BUDGET_STYLE = (
    "Which style do you want for your budget: zero-based or 50/30/20? "
    "Pick one below, or tell me in your own words."
)


def _history(reply: str, *, skill: bool = True) -> list[dict]:
    history: list[dict] = [{"role": "user", "content": "__ONBOARDING_INIT__"}]
    if skill:
        history.append(
            {
                "role": "user",
                "content": "[SKILL INVOCATION: onboarding-new-user (onboarding-new-user)]\n...",
                "_onw_skill": {
                    "skill_id": "onboarding-new-user",
                    "skill_name": "onboarding-new-user",
                    "source": "model",
                },
            }
        )
    history.append({"role": "assistant", "content": reply})
    return history


@pytest.fixture
def client(monkeypatch):
    app = FastAPI()
    app.include_router(cs.router)
    return TestClient(app)


def _post(client, monkeypatch, history):
    async def no_model(*_a, **_k):
        raise AssertionError("the model must not be called for the fixed menu")

    monkeypatch.setattr(cs.assistant, "_load_session", lambda _sid: history)
    monkeypatch.setattr(cs, "complete_model", no_model)
    return client.post("/api/chat/suggestions", json={"session_id": "s-1"})


def test_the_first_menu_gets_the_fixed_chips_without_a_model_call(client, monkeypatch):
    r = _post(client, monkeypatch, _history(MENU))
    assert r.status_code == 200
    assert r.json()["suggestions"] == list(ai_cs.ONBOARDING_MENU_CHOICES)


def test_a_later_option_question_in_onboarding_is_not_the_menu(client, monkeypatch):
    r = _post(client, monkeypatch, _history(BUDGET_STYLE))
    assert r.json() == {"suggestions": []}  # fell through to the (failing) generator -> no chips


def test_the_menu_wording_outside_onboarding_is_not_the_menu(client, monkeypatch):
    r = _post(client, monkeypatch, _history(MENU, skill=False))
    assert r.json() == {"suggestions": []}


def test_the_connector_surface_uses_the_same_fixed_menu(monkeypatch):
    import asyncio

    monkeypatch.setattr(ai_cs.assistant, "_load_session", lambda _sid: _history(MENU))
    assert asyncio.run(ai_cs.followup_suggestions("s-1")) == list(ai_cs.ONBOARDING_MENU_CHOICES)


def test_every_chip_is_a_complete_sendable_message_and_names_a_branch():
    """The chips are sent verbatim; each must hit one of the skill's branches by its words."""
    choices = " | ".join(ai_cs.ONBOARDING_MENU_CHOICES).lower()
    for stem in ("money goes", "everything i have", "budget", "will last", "investments"):
        assert stem in choices
    assert all(c and not c.endswith(("…", "...")) for c in ai_cs.ONBOARDING_MENU_CHOICES)
