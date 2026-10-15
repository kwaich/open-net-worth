"""Canonical-markdown → per-channel dialect renderer (multi-channel formatting layer)."""

from __future__ import annotations

from backend.connectors.msgfmt import render


def test_bold_italic_code_per_dialect():
    md = "**bold** _it_ `co`"
    assert render(md, "plain") == "bold it co"


def test_links_per_dialect():
    md = "see [Benzinga](https://news.google.com/x)"
    assert render(md, "plain") == "see Benzinga (https://news.google.com/x)"


def test_telegram_target_delegates_to_html():
    out = render("**hi** [x](https://x.com)", "telegram")
    assert "<b>hi</b>" in out
    assert '<a href="https://x.com">x</a>' in out


def test_empty_and_unknown_target():
    assert render("", "plain") == ""
    # unknown target falls back to plain (markers stripped), never raw markdown
    assert render("**b**", "sms") == "b"


def test_email_renders_html_multipart_body():
    out = render("**bold** [FT](https://ft.com) & <x>", "email")
    assert out.startswith('<div style="white-space:pre-wrap')
    assert "<strong>bold</strong>" in out
    assert '<a href="https://ft.com">FT</a>' in out
    assert "&amp; &lt;x&gt;" in out  # text html-escaped


def test_matrix_renders_html_subset_with_br():
    out = render("line one\n**b** [FT](https://ft.com)\nline three", "matrix")
    assert "<strong>b</strong>" in out
    assert '<a href="https://ft.com">FT</a>' in out
    assert "<br/>" in out  # newlines → <br/> outside <pre>


def test_matrix_code_block_no_double_break():
    out = render("intro\n```\na\nb\n```\nend", "matrix")
    assert "<pre>a\nb</pre>" in out  # newlines INSIDE pre preserved, not <br/>-doubled
    assert "intro<br/>" in out  # newline OUTSIDE pre converted
