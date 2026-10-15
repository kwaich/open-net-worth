"""Generic follow-up suggestions: adapter capability + toggle + Telegram inline-keyboard delivery."""

from __future__ import annotations

import asyncio

from backend.connectors.base import BaseChannelAdapter


def test_base_suggestions_off_by_default():
    class A(BaseChannelAdapter):
        async def _deliver(self, c, p):
            pass

    a = A()
    assert a.supports_suggestions is False
    assert a.suggestions_enabled() is False


def test_telegram_supports_suggestions_and_toggle(tmp_path, monkeypatch):
    from backend.connectors.telegram.handlers import _TelegramAdapter
    from backend.db.telegram_config_store import TelegramConfigStore

    # telegram_config is DB-backed now (Wave 5) — conftest's autouse Tier-2 table clear
    # already isolates it per test, no path kwarg needed.
    store = TelegramConfigStore()
    assert store.suggestions_enabled() is False  # opt-in
    store.set_suggestions(True)
    assert store.suggestions_enabled() is True

    monkeypatch.setattr(
        "backend.db.telegram_config_store.TelegramConfigStore",
        lambda: __import__("types").SimpleNamespace(suggestions_enabled=lambda: True),
    )
    a = _TelegramAdapter(bot=None)
    assert a.supports_suggestions is True
    assert a.suggestions_enabled() is True


def test_suggestions_ride_the_reply_as_reply_keyboard():
    """No separate carrier bubble: the follow-up suggestions attach to the ANSWER message itself as
    a native REPLY keyboard on the last chunk (owner: 'why is there a lightbulb as a separate
    message'). Tapping a chip sends the text as a normal message."""
    from backend.connectors.telegram.handlers import _TelegramAdapter

    sends = []

    class _Bot:
        async def send_message(self, **kw):
            sends.append(kw)

    a = _TelegramAdapter(_Bot())
    asyncio.run(
        a.send_text(
            "42", "Your net worth is up.", suggestions=["Show breakdown", "Compare to last month"]
        )
    )
    assert len(sends) == 1  # ONE message — the reply, no separate 💡 bubble
    assert "Your net worth is up." in sends[0]["text"]
    kb = sends[0]["reply_markup"].keyboard  # ReplyKeyboardMarkup rides the reply
    assert kb[0][0].text == "Show breakdown"
    assert sends[0]["reply_markup"].one_time_keyboard is True


def test_send_text_without_suggestions_takes_normal_path():
    from backend.connectors.telegram.handlers import _TelegramAdapter

    sends = []

    class _Bot:
        token = ""  # no token → rich path skipped → legacy _deliver

        async def send_message(self, **kw):
            sends.append(kw)

    a = _TelegramAdapter(_Bot())
    asyncio.run(a.send_text("42", "plain reply"))
    assert len(sends) == 1 and sends[0].get("reply_markup") is None


def test_bot_commands_include_suggestions():
    from backend.connectors.telegram.handlers import BOT_COMMANDS

    assert "suggestions" in [c for c, _ in BOT_COMMANDS]


def test_rich_table_reply_with_suggestions_stays_rich(monkeypatch):
    """The bug fix: a TABLE reply with suggestions must render as a rich message (sendRichMessage
    with reply_markup), NOT degrade to legacy text (owner: 'table is not shown after delete')."""
    from backend.connectors.telegram.handlers import _TelegramAdapter

    calls = {}

    async def _fake_send_rich_html(token, chat_id, html, reply_markup=None):
        calls["html"] = html
        calls["reply_markup"] = reply_markup
        return True

    monkeypatch.setattr("backend.connectors.telegram.rich.send_rich_html", _fake_send_rich_html)

    class _Bot:
        token = "T"

        async def send_message(self, **kw):
            calls["legacy_used"] = True

    a = _TelegramAdapter(_Bot())
    table = "| A | B |\n|---|---|\n| 1 | 2 |"
    asyncio.run(a.send_text("42", table, suggestions=["More detail"]))
    assert "<table" in calls["html"]  # rendered as a rich table, not legacy text
    assert calls["reply_markup"] is not None  # the reply keyboard rode along
    assert "legacy_used" not in calls  # did NOT fall back to legacy send_message
