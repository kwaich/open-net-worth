"""Connector /stop routing (chat.run.handle_inbound) + Telegram command registration.

Complements test_turn_registry.py (the cancellation mechanism): here we prove the SEAM routes a
``/stop`` message to a cancel instead of a new turn, and only for the explicit command — a bare
"stop" is normal input (owner: /stop only). The generic seam covers Slack/Discord/WhatsApp;
Telegram gets its own CommandHandler, asserted registered.
"""

from __future__ import annotations

import pytest

from backend.connectors import turn_registry
from backend.connectors.chat import run as chat_run
from backend.connectors.chat.base import TRUST_OPERATOR, InboundMessage


class _FakeAdapter:
    connector = "slack"
    trust_level = TRUST_OPERATOR  # operator → no rate-limit path

    def __init__(self):
        self.sent: list[str] = []

    def access_policy(self):
        return "open"  # POLICY_OPEN → allowed without an allowlist entry

    def is_allowed(self, sender_id):
        return True

    async def send_text(self, channel_id, text, **kw):
        self.sent.append(text)


def _inbound(text: str) -> InboundMessage:
    return InboundMessage(channel_id="C1", sender_id="U1", text=text)


@pytest.mark.parametrize(
    "text,expected",
    [
        ("/stop", True),
        ("/STOP", True),
        ("  /stop  ", True),
        ("/stop@mybot", True),
        ("/stop now", True),
        ("stop", False),  # bare word is NOT a trigger (owner: /stop only)
        ("please stop", False),
        ("abort", False),
        ("/status", False),
        ("what does /stop do?", False),
        ("", False),
    ],
)
def test_is_stop_command(text, expected):
    assert chat_run._is_stop_command(text) is expected


@pytest.mark.asyncio
async def test_stop_message_cancels_and_never_starts_a_turn(monkeypatch):
    calls = {"pm": 0, "stop_args": None}

    async def _pm(*a, **k):
        calls["pm"] += 1

    def _stop(connector, channel_id):
        calls["stop_args"] = (connector, channel_id)
        return True  # pretend a turn was running

    monkeypatch.setattr(chat_run, "process_message", _pm)
    monkeypatch.setattr(turn_registry, "stop", _stop)

    adapter = _FakeAdapter()
    await chat_run.handle_inbound(adapter, _inbound("/stop"))

    assert calls["stop_args"] == ("slack", "C1")  # cancelled THIS channel
    assert calls["pm"] == 0  # no new turn started
    assert adapter.sent == ["⏹ Stopped."]


@pytest.mark.asyncio
async def test_stop_with_nothing_running_says_so(monkeypatch):
    monkeypatch.setattr(chat_run, "process_message", lambda *a, **k: None)
    monkeypatch.setattr(turn_registry, "stop", lambda *a: False)  # nothing to cancel
    adapter = _FakeAdapter()
    await chat_run.handle_inbound(adapter, _inbound("/stop"))
    assert adapter.sent == ["Nothing is running to stop."]


@pytest.mark.asyncio
async def test_normal_message_runs_a_turn_and_does_not_stop(monkeypatch):
    calls = {"pm": 0, "stop": 0}

    async def _pm(*a, **k):
        calls["pm"] += 1

    monkeypatch.setattr(chat_run, "process_message", _pm)
    monkeypatch.setattr(turn_registry, "stop", lambda *a: calls.__setitem__("stop", 1) or True)
    monkeypatch.setattr(chat_run.session_map, "get_or_create_session", lambda *a, **k: "sess1")
    monkeypatch.setattr(chat_run.session_map, "get_effort", lambda *a, **k: "medium")

    adapter = _FakeAdapter()
    await chat_run.handle_inbound(adapter, _inbound("what is my net worth?"))

    assert calls["pm"] == 1  # a real turn ran
    assert calls["stop"] == 0  # nothing was cancelled


def test_telegram_registers_a_stop_command():
    from backend.connectors.telegram.handlers import BOT_COMMANDS, handle_stop

    assert "stop" in [c for c, _ in BOT_COMMANDS]
    assert callable(handle_stop)
