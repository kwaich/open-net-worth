"""Telegram live streaming (edit-in-place) — the adapter capability + config toggle."""

from __future__ import annotations

import asyncio


def test_streaming_config_toggle():
    from backend.db.telegram_config_store import TelegramConfigStore

    store = TelegramConfigStore()
    assert store.streaming_enabled() is True  # default on
    store.set_streaming(False)
    assert store.streaming_enabled() is False
    store.set_streaming(True)
    assert store.streaming_enabled() is True


def test_adapter_supports_streaming_follows_config(monkeypatch):
    from backend.connectors.telegram.handlers import _TelegramAdapter

    class _Store:
        def __init__(self, on):
            self._on = on

        def streaming_enabled(self):
            return self._on

    monkeypatch.setattr(
        "backend.db.telegram_config_store.TelegramConfigStore", lambda: _Store(True)
    )
    assert _TelegramAdapter(bot=None).supports_streaming is True
    monkeypatch.setattr(
        "backend.db.telegram_config_store.TelegramConfigStore", lambda: _Store(False)
    )
    assert _TelegramAdapter(bot=None).supports_streaming is False


def test_stream_finalize_ordinary_edits_in_place(monkeypatch):
    from backend.connectors.telegram.handlers import _TelegramAdapter

    a = _TelegramAdapter(bot=None)
    edits = []
    monkeypatch.setattr(
        _TelegramAdapter, "stream_update", lambda self, c, m, t: _noop(edits.append(t))
    )
    asyncio.run(a.stream_finalize("1", "10", "just plain prose"))
    assert edits == ["just plain prose"]  # ordinary → in-place HTML edit


def test_stream_finalize_rich_edits_in_place_no_delete(monkeypatch):
    """A table finalizes by editing the SAME preview into a rich message (Bot API 10.2) — no
    delete, no flicker. This is the fix for 'streaming deletes the previous content'."""
    from backend.connectors.telegram.handlers import _TelegramAdapter

    a = _TelegramAdapter(bot=None)
    calls = {"edit_rich": [], "delete": [], "send_text": []}

    async def _edit_rich(self, c, m, t):
        calls["edit_rich"].append((m, t))
        return True  # in-place rich edit succeeds

    monkeypatch.setattr(_TelegramAdapter, "_edit_rich", _edit_rich)
    monkeypatch.setattr(
        _TelegramAdapter, "delete_message", lambda self, c, m: _noop(calls["delete"].append(m))
    )
    monkeypatch.setattr(
        _TelegramAdapter, "send_text", lambda self, c, t: _noop(calls["send_text"].append(t))
    )
    asyncio.run(a.stream_finalize("1", "11", "| A | B |\n|---|---|\n| 1 | 2 |"))
    assert len(calls["edit_rich"]) == 1  # edited in place
    assert calls["delete"] == [] and calls["send_text"] == []  # NOT deleted / re-sent


def test_stream_finalize_rich_falls_back_to_resend_if_edit_fails(monkeypatch):
    from backend.connectors.telegram.handlers import _TelegramAdapter

    a = _TelegramAdapter(bot=None)
    calls = {"delete": [], "send_text": []}

    async def _edit_rich_fail(self, c, m, t):
        return False  # in-place rich edit fails → fall back to delete + fresh send

    monkeypatch.setattr(_TelegramAdapter, "_edit_rich", _edit_rich_fail)
    monkeypatch.setattr(
        _TelegramAdapter, "delete_message", lambda self, c, m: _noop(calls["delete"].append(m))
    )
    monkeypatch.setattr(
        _TelegramAdapter, "send_text", lambda self, c, t: _noop(calls["send_text"].append(t))
    )
    asyncio.run(a.stream_finalize("1", "12", "| A | B |\n|---|---|\n| 1 | 2 |"))
    assert calls["delete"] == ["12"] and len(calls["send_text"]) == 1


async def _noop(_=None):
    return None


def test_bot_commands_include_streaming():
    from backend.connectors.telegram.handlers import BOT_COMMANDS

    names = [c for c, _ in BOT_COMMANDS]
    assert "streaming" in names and "help" in names and "effort" in names
