"""An empty book's first session is offered the tools onboarding reaches for.

Persona QA 2026-10-13: every onboarding spent a find_tools round (15–20s) on create_account,
show_guide or the skill's references before its first real step.
"""

from __future__ import annotations

import pytest

import backend.ai.tools  # noqa: F401  (registers every tool)
from backend.ai import assistant
from backend.ai.tools._extra_groups_scope import get_extra_names, set_extra_names
from backend.ai.tools._groups import ONBOARDING_TOOL_NAMES
from backend.services import accounts as accounts_service


@pytest.fixture(autouse=True)
def _clean_loaded_set():
    yield
    set_extra_names(frozenset())  # a ContextVar: left set, it rides into every later test


def _offered() -> set[str]:
    return {t["name"] for t in assistant._active_tools()}


def test_an_empty_book_offers_the_onboarding_tools_from_the_first_turn(db_isolated, monkeypatch):
    saved = {}
    monkeypatch.setattr(assistant, "upsert_session_meta", lambda sid, **kw: saved.update(kw))
    set_extra_names(frozenset())
    assistant._seed_onboarding_tools("s1")
    assert ONBOARDING_TOOL_NAMES <= _offered()
    assert ONBOARDING_TOOL_NAMES <= set(saved["extra_tool_names"])  # sticky for the session


def test_a_book_with_an_account_is_not_seeded(db_isolated, monkeypatch):
    monkeypatch.setattr(assistant, "upsert_session_meta", lambda sid, **kw: None)
    accounts_service.create_account(
        {"name": "QA Bank", "type": "cash", "currency": "SGD", "initial_balance": 0}
    )
    set_extra_names(frozenset())
    assistant._seed_onboarding_tools("s1")
    assert not (ONBOARDING_TOOL_NAMES & get_extra_names())
