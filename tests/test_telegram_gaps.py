"""Hermes-parity gap closures: rich-unsafe guards (crash/garble/cap), retry_after flood backoff,
keep-typing capability, and inbound split-message batching."""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace


# ── #1 + #8: rich-unsafe guards ────────────────────────────────────────────────
def test_rich_unsafe_guards():
    from backend.connectors.telegram.rich import rich_unsafe

    # math inside <details> crashes TDesktop → unsafe
    assert rich_unsafe("<details><summary>x</summary>\n$$E=mc^2$$\n</details>")
    # CJK garbles in rich → unsafe
    assert rich_unsafe("| 资产 | 价值 |\n|---|---|\n| 现金 | 100 |")
    # over the 32k cap → unsafe
    assert rich_unsafe("x" * 40000)
    # a normal english table → SAFE
    assert not rich_unsafe("| A | B |\n|---|---|\n| 1 | 2 |")
    # math NOT inside details → safe (block math renders fine)
    assert not rich_unsafe("The identity $$E=mc^2$$ on its own line")


def test_rich_eligible_skips_unsafe(monkeypatch):
    from backend.connectors.telegram.handlers import _TelegramAdapter

    a = _TelegramAdapter(bot=None)
    assert a._rich_eligible("| A | B |\n|---|---|\n| 1 | 2 |")  # normal table → rich
    assert not a._rich_eligible("| 资产 | 值 |\n|---|---|\n| 现 | 1 |")  # CJK table → NOT rich


# ── #2: retry_after flood backoff ──────────────────────────────────────────────
def test_stream_update_honors_flood_backoff():
    from backend.connectors.telegram.handlers import _TelegramAdapter

    edits = []

    class _Bot:
        async def edit_message_text(self, **kw):
            edits.append(kw)

    a = _TelegramAdapter(_Bot())
    a._flood_until = time.monotonic() + 60  # pretend a 429 set a backoff window
    asyncio.run(a.stream_update("1", "10", "hello"))
    assert edits == []  # edit skipped while flood window is open


def test_stream_update_sets_flood_on_retry_after():
    from backend.connectors.telegram.handlers import _TelegramAdapter

    class _Flood(Exception):
        retry_after = 5

    class _Bot:
        async def edit_message_text(self, **kw):
            raise _Flood()

    a = _TelegramAdapter(_Bot())
    asyncio.run(a.stream_update("1", "10", "hello"))
    assert a._flood_until > time.monotonic()  # backoff window set from retry_after


# ── #5: keep-typing capability ─────────────────────────────────────────────────
def test_telegram_supports_typing_and_media():
    from backend.connectors.telegram.handlers import _TelegramAdapter

    assert _TelegramAdapter.supports_typing is True
    assert _TelegramAdapter.supports_media is True


# ── #6: inbound split-message batching ─────────────────────────────────────────
def _fake_update(chat_id, text, mid=1):
    return SimpleNamespace(
        effective_chat=SimpleNamespace(id=chat_id),
        effective_user=SimpleNamespace(username="u", first_name="U"),
        message=SimpleNamespace(text=text, message_id=mid),
    )


def test_short_message_dispatches_immediately(monkeypatch):
    import backend.connectors.telegram.handlers as h

    dispatched = []

    async def _fake_dispatch(bot, cid, mid, text, react=True, **_kw):
        dispatched.append(text)

    monkeypatch.setattr(h, "_dispatch_text", _fake_dispatch)
    h._text_batches.clear()
    ctx = SimpleNamespace(bot=SimpleNamespace())
    asyncio.run(h.handle_message(_fake_update(7, "hi there"), ctx))
    assert dispatched == ["hi there"]  # immediate, no batching
    assert 7 not in h._text_batches


def test_long_message_is_buffered(monkeypatch):
    import backend.connectors.telegram.handlers as h

    dispatched = []

    async def _fake_dispatch(bot, cid, mid, text, react=True, **_kw):
        dispatched.append(text)

    class _Bot:
        async def set_message_reaction(self, **kw):
            pass

        async def send_chat_action(self, **kw):
            pass

    monkeypatch.setattr(h, "_dispatch_text", _fake_dispatch)
    monkeypatch.setattr(
        h, "_TelegramAdapter", lambda bot: SimpleNamespace(is_allowed=lambda s: False)
    )
    h._text_batches.clear()
    ctx = SimpleNamespace(bot=_Bot())
    big = "x" * 4100  # over the split threshold
    asyncio.run(h.handle_message(_fake_update(8, big), ctx))
    assert dispatched == []  # NOT dispatched immediately — buffered
    assert 8 in h._text_batches and h._text_batches[8]["parts"] == [big]
    # cleanup the debounce task
    h._text_batches[8]["task"].cancel()
    h._text_batches.clear()
