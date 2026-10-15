"""Telegram formatter — markdown → Telegram HTML. Focus: hyperlink rendering (a real bug: analyst
source links showed as raw giant Google-News URLs instead of clickable outlet names)."""

from __future__ import annotations

from backend.connectors.telegram.formatter import markdown_to_telegram_html


def test_markdown_link_becomes_anchor():
    out = markdown_to_telegram_html("See [Benzinga](https://news.google.com/rss/CBMiAB_cd-ef)")
    assert '<a href="https://news.google.com/rss/CBMiAB_cd-ef">Benzinga</a>' in out


def test_link_url_with_underscores_not_italicized():
    """A base64 Google-News redirect URL is full of _ and - — the italic/bold passes must not
    mangle it (it's stashed behind a sentinel before those run)."""
    out = markdown_to_telegram_html("[Reuters](https://news.google.com/a_b_c-d_e)")
    assert 'href="https://news.google.com/a_b_c-d_e"' in out
    assert "<i>" not in out


def test_link_text_and_url_are_escaped():
    out = markdown_to_telegram_html("[A & B](https://x.com/?q=1&r=2)")
    # & escaped in both the visible text and the href
    assert "A &amp; B" in out
    assert "q=1&amp;r=2" in out


def test_bold_italic_code_still_work():
    assert "<b>hi</b>" in markdown_to_telegram_html("**hi**")
    assert "<i>hi</i>" in markdown_to_telegram_html("_hi_")
    assert "<code>x</code>" in markdown_to_telegram_html("`x`")
