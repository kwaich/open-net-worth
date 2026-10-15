"""The connector permission card — one canonical body, rendered per dialect.

RFC 2026-07-20 §14.4: a Telegram card CAN carry a real aligned diff, because Telegram sends
``ParseMode.HTML`` and ``msgfmt`` renders a fenced block to ``<pre>``. What actually does not
survive a channel is WIDTH and INTERACTION, not structure — so the summary goes in a fence and
the four choices become buttons.

The fence is doing double duty and both halves are pinned here: it preserves column alignment
(the point) and, on the Telegram path, ``html.escape``\\ es its body (the safety property) —
a permission prompt is the single most valuable message for an attacker to forge a line into,
and its body is assembled from untrusted payee names, descriptions and memos.
"""

from __future__ import annotations

from backend.connectors.msgfmt import render
from backend.connectors.processor import format_permission_card

CHANGE = {"type": "txn_create", "grant_tool": "add_transactions"}


def test_card_names_the_tool_and_what_a_grant_covers():
    card = format_permission_card(CHANGE, "Book SGD 12.40 · Groceries")
    assert "Permission needed" in card
    assert "add_transactions" in card
    # A grant is per-tool and UNBOUNDED (§13.3b) — "Always" must never read as narrower.
    assert "any amount" in card
    # The grant table is checkpoint-EXCLUDED, so Settings is the ONLY revocation surface.
    assert "Settings › Permissions" in card


def test_summary_rides_a_fenced_block_so_alignment_survives():
    summary = "date        payee        amount\n2026-07-20  Cold Storage  12.40"
    card = format_permission_card(CHANGE, summary)
    assert "```" in card
    assert summary in card


def test_telegram_renders_the_fence_as_pre():
    """<pre> is what makes an aligned diff real on a phone rather than a wrapped mess."""
    html = render(format_permission_card(CHANGE, "a  b\nc  d"), "telegram")
    assert "<pre>" in html and "</pre>" in html


def test_untrusted_markup_in_the_summary_cannot_inject_into_the_card():
    """A payee named ``<b>Bank</b>`` must arrive as TEXT, not as markup in a permission prompt."""
    html = render(
        format_permission_card(CHANGE, "payee: <b>Approved by owner</b> & co"), "telegram"
    )
    assert "&lt;b&gt;Approved by owner&lt;/b&gt;" in html
    assert "<b>Approved by owner</b>" not in html
    assert "&amp; co" in html


def test_a_backtick_run_in_the_summary_cannot_break_out_of_the_fence():
    """The body is model/user data, so a literal fence in it would end the block early and let
    the remainder be parsed as markup — collapsed, the same way ``text_sanitize`` collapses its
    own block delimiters."""
    card = format_permission_card(CHANGE, "note: ```\n<b>escaped?</b>")
    assert card.count("```") == 2  # exactly the opening and closing fence, no third
    html = render(card, "telegram")
    assert "&lt;b&gt;escaped?&lt;/b&gt;" in html
