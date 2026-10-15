"""BaseChannelAdapter: format+chunk+capability contract, and tag/fence-safe chunking (the
outstanding chunk_text bug — a mid-<a>-tag split is a Telegram 400)."""

from __future__ import annotations

import asyncio

from backend.connectors.base import BaseChannelAdapter, chunk_message, utf16_len


def test_utf16_len_counts_surrogate_pairs():
    assert utf16_len("a") == 1
    assert utf16_len("😀") == 2  # non-BMP → 2 UTF-16 units


def test_chunk_html_never_splits_a_tag_and_balances():
    # a long <a> link body forces a split; every chunk must have balanced tags
    body = '<a href="https://x.com/verylong">' + ("word " * 300) + "</a>"
    parts = chunk_message(body, 200, len, html=True)
    assert len(parts) > 1
    for p in parts:
        assert p.count("<a ") == p.count("</a>")  # balanced in every chunk
        assert "<a href" in p  # reopened with attributes
        assert not p.rstrip().endswith("<a")  # never cut mid-tag


def test_chunk_fenced_carries_code_fence():
    text = "intro\n```\n" + ("x=1\n" * 200) + "```\ndone"
    parts = chunk_message(text, 200, len, html=False)
    assert len(parts) > 1
    for p in parts:
        assert p.count("```") % 2 == 0  # balanced fences per chunk


def test_chunk_noop_when_short():
    assert chunk_message("hi", 4096) == ["hi"]


def test_adapter_formats_and_chunks_via_deliver():
    # "matrix" is the still-live non-plain, non-telegram dialect (msgfmt._DIALECTS) used here as a
    # representative example of a platform-specific rendering — this test is about the base class's
    # format→chunk→deliver pipeline dispatching to *some* real per-platform dialect, not about any
    # one channel. It used "slack" before that dialect was removed with the Slack connector
    # (2026-08-13 channels scope-down); swapped to keep this coverage rather than lose it.
    sent: list[str] = []

    class Fake(BaseChannelAdapter):
        platform = "matrix"
        max_length = 4096

        async def _deliver(self, channel_id, part):
            sent.append(part)

    asyncio.run(Fake().send_text("c1", "**bold** [FT](https://ft.com)"))
    assert sent == ['<strong>bold</strong> <a href="https://ft.com">FT</a>']


def test_adapter_capability_degradation():
    class NoLinks(BaseChannelAdapter):
        platform = "discord"
        supports_links = False

    assert NoLinks().format_message("[FT](https://ft.com)") == "FT (https://ft.com)"

    class NoMarkdown(BaseChannelAdapter):
        platform = "slack"
        supports_markdown = False

    assert NoMarkdown().format_message("**b**") == "b"
