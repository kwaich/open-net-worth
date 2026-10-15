"""Telegram Rich Messages — copies Hermes's construct-branch (minus streaming): rich ONLY for
tables / task-lists / <details> / block-math; ordinary replies stay on the legacy HTML path
(owner directive 2026-07-20: copy exactly hermes agent handling)."""

from __future__ import annotations

import asyncio

from backend.connectors.telegram.formatter import markdown_to_rich_html
from backend.connectors.telegram.rich import needs_rich_rendering, rich_payload


def test_needs_rich_only_for_degrading_constructs():
    # rich constructs
    assert needs_rich_rendering("| A | B |\n|---|---|\n| 1 | 2 |")  # pipe table
    assert needs_rich_rendering("- [ ] todo\n- [x] done")  # task list
    assert needs_rich_rendering("<details><summary>x</summary>y</details>")  # details
    assert needs_rich_rendering("The formula $$E=mc^2$$ holds")  # block math
    # ordinary → NOT rich
    assert not needs_rich_rendering("**bold** and _italic_ and a [link](https://x.com)")
    assert not needs_rich_rendering("- simple\n- bullet\n- list")
    assert not needs_rich_rendering("plain prose, nothing special")
    assert not needs_rich_rendering("")


def test_rich_payload_uses_html_field():
    p = rich_payload("123", "<table></table>")
    assert p == {"chat_id": "123", "rich_message": {"html": "<table></table>"}}


def test_markdown_to_rich_html_emits_real_blocks():
    out = markdown_to_rich_html("| A | B |\n|---|---|\n| 1 | 2 |")
    assert "<table bordered striped>" in out and "<th>A</th>" in out and "<td>1</td>" in out
    tl = markdown_to_rich_html("- [ ] todo\n- [x] done")
    assert '<input type="checkbox">' in tl and '<input type="checkbox" checked>' in tl
    assert "<tg-math-block>" in markdown_to_rich_html("$$E=mc^2$$")
    assert "<b>bold</b>" in markdown_to_rich_html("**bold** prose")


class _FakeBot:
    token = "T"

    def __init__(self):
        self.sent = []

    async def send_message(self, **kw):  # the HTML fallback path
        self.sent.append(kw)


def _adapter(bot):
    from backend.connectors.telegram.handlers import _TelegramAdapter

    return _TelegramAdapter(bot)


def test_ordinary_reply_never_attempts_rich(monkeypatch):
    bot = _FakeBot()
    a = _adapter(bot)
    calls = {"rich": 0}

    async def _rich(self, channel_id, text, reply_markup=None):
        calls["rich"] += 1
        return True

    monkeypatch.setattr(type(a), "_send_rich", _rich, raising=True)
    asyncio.run(a.send_text("42", "**just bold**, no table"))
    assert calls["rich"] == 0  # ordinary → rich not even attempted
    assert len(bot.sent) == 1  # went via HTML path
    assert "<b>just bold</b>" in bot.sent[0]["text"]


def test_table_reply_uses_rich(monkeypatch):
    bot = _FakeBot()
    a = _adapter(bot)

    async def _rich(self, channel_id, text, reply_markup=None):
        _rich.got = text
        return True

    monkeypatch.setattr(type(a), "_send_rich", _rich, raising=True)
    asyncio.run(a.send_text("42", "| A | B |\n|---|---|\n| 1 | 2 |"))
    assert "|---|---|" in _rich.got  # raw markdown passed through
    assert bot.sent == []  # HTML fallback NOT used


def test_rich_failure_falls_back_and_latches(monkeypatch):
    bot = _FakeBot()
    a = _adapter(bot)

    async def _rich_fail(token, chat_id, md, reply_markup=None):
        return False

    monkeypatch.setattr("backend.connectors.telegram.rich.send_rich_html", _rich_fail)
    table = "| A | B |\n|---|---|\n| 1 | 2 |"
    asyncio.run(a.send_text("42", table))
    assert a._rich_send_disabled is True  # latched off after failure
    assert len(bot.sent) == 1  # fell back to HTML
