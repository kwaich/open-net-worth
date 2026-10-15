"""Characterization: Telegram inbound→reply over the chat-channel seam is byte-identical.

Pins the contract the seam-extraction (RFC chat-channel-seam S1) must not change:
  * ``handle_message`` builds an ``InboundMessage`` and funnels through ``handle_inbound``
    → ``process_message`` with the SAME ``(connector, channel_id, session_id, text, effort)``
    it always passed (chat_id stringified, the session-mapped UUID, the per-user effort).
  * Telegram runs at ``trust_level="operator"`` — the FULL tool set — so no tool offer is
    NEVER activated for a Telegram turn (the operator path is unchanged; only NEW channels
    like Slack are headless-scoped).
  * A non-allowlisted chat is rejected before any turn runs.

These freeze today's Telegram behavior so a regression in the seam trips immediately.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from backend.connectors.chat import run as chat_run
from backend.connectors.telegram import handlers


class _Bot:
    def __init__(self):
        self.set_message_reaction = AsyncMock()
        self.send_chat_action = AsyncMock()
        self.send_message = AsyncMock()


def _update(chat_id: int, text: str):
    return SimpleNamespace(
        effective_chat=SimpleNamespace(id=chat_id),
        effective_user=SimpleNamespace(username="alice", first_name="Alice"),
        message=SimpleNamespace(text=text, message_id=7),
    )


@pytest.fixture
def isolated(db_isolated, monkeypatch):
    """Isolate the connector session map + open the Telegram allowlist (no env token).

    session_map is DB-backed now (a "connector_sessions" namespace row in secret_config,
    Wave 5D1, JSON->DB) — db_isolated gives it a fresh, isolated sqlite file; no more
    ``_MAP_FILE`` module attribute to repoint.

    ``enabled: True`` is required here (security review 2026-08-17, finding B):
    ``TelegramConfigStore.access_policy()`` now fail-closes to ``disabled`` whenever the
    connector is paused/off, and the store's own ``DEFAULTS`` has ``enabled: False`` — so a
    seam test that never configures the store would otherwise have every message silently
    dropped before ``process_message`` is reached, which is not what this file tests."""
    from backend.db.telegram_config_store import TelegramConfigStore

    TelegramConfigStore().update({"enabled": True})
    monkeypatch.setattr(handlers, "is_allowed", lambda cid: True)
    yield


@pytest.mark.asyncio
async def test_handle_message_calls_process_with_operator_scope(isolated, monkeypatch):
    captured = {}
    headless_seen = {"value": "unset"}

    async def _fake_process(adapter, channel_id, session_id, text, effort, **kw):
        # Capture the exact positional contract + whether a headless scope is active.
        # arg 1 is the duck-typed connector adapter (its .connector names the channel).
        from backend.ai.tools import _skill_scope

        captured.update(
            connector=getattr(adapter, "connector", None),
            channel_id=channel_id,
            session_id=session_id,
            text=text,
            effort=effort,
            kwargs=kw,
        )
        headless_seen["value"] = _skill_scope.offer_tool_names()

    # The seam (run.handle_inbound) calls process_message imported into chat.run.
    monkeypatch.setattr(chat_run, "process_message", _fake_process)

    bot = _Bot()
    ctx = SimpleNamespace(bot=bot)
    await handlers.handle_message(_update(123456, "what's my net worth?"), ctx)

    assert captured["channel_id"] == "123456"  # chat_id stringified
    assert isinstance(captured["session_id"], str) and len(captured["session_id"]) >= 8
    assert captured["text"] == "what's my net worth?"
    assert captured["effort"] == "low"  # session_map default
    # The connector adapter passed as arg 1 identifies the channel (post-seam: a .connector
    # attribute; the duck-typed send interface is unchanged).
    assert captured["connector"] in ("telegram", None)
    # Operator trust → the full tool set; no caller-supplied offer is ever set for Telegram.
    assert headless_seen["value"] is None


@pytest.mark.asyncio
async def test_handle_message_rejects_non_allowlisted(isolated, monkeypatch):
    monkeypatch.setattr(handlers, "is_allowed", lambda cid: False)
    called = {"n": 0}

    async def _fake_process(*a, **k):
        called["n"] += 1

    monkeypatch.setattr(chat_run, "process_message", _fake_process)

    bot = _Bot()
    ctx = SimpleNamespace(bot=bot)
    await handlers.handle_message(_update(999, "hi"), ctx)
    assert called["n"] == 0  # no turn ran
