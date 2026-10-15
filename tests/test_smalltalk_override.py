"""
Harness half of the response-calibration fix (ADR
2026-07-11-response-calibration-effort-as-ceiling): a pure-smalltalk turn gets the
SMALLTALK_STYLE response-style line regardless of the effort dial — deterministic, in
the dispatch path (assistant._resolve_style), shared by web chat, connectors, and
responder turns.

Safety property under test: the override can NEVER catch a substantive request — extra
words, digits, mention context, attachments, and flow-control words (yes/ok/confirmed)
all fall through to the normal effort style. No LLM, fully deterministic.
"""

from __future__ import annotations

from backend.ai.assistant import _resolve_style
from backend.ai.smalltalk import (
    OVER_ANSWER_WORD_CAP,
    SMALLTALK_STYLE,
    is_smalltalk_text,
    is_smalltalk_turn,
    looks_over_answered,
)

_SCREEN_BLOCK = (
    "[Currently on screen — 2026 Tax Estimate — the user is looking at THIS right now…]\n"
    "facts: estimated_tax_owed=SGD 4,321.00"
)


def _turn(content):
    return [{"role": "user", "content": content}]


# ── is_smalltalk_text truth table ────────────────────────────────────────────────


def test_smalltalk_text_positives():
    for text in [
        "hello",
        "Hi!",
        "hey",
        "HELLO",
        "how are you?",
        "How's it going?",
        "thanks",
        "Thank you so much!",
        "good morning",
        "Good morning, Lucius!",
        "hey lucius",
        "hello 👋",
        "what's up",
        "bye",
        "see you later",
        "lol",
        "test",
    ]:
        assert is_smalltalk_text(text), text


def test_smalltalk_text_negatives():
    for text in [
        # greeting + substance must never be down-styled
        "hi — how's my portfolio doing?",
        "hello, can you sell everything?",
        "hey, quick question about ONWAI",
        "thanks, now show me my spending",
        # questions about data
        "how are my investments doing?",
        "what's my net worth",
        # flow-control (confirm-gated writes) — deliberately excluded
        "yes",
        "no",
        "ok",
        "okay",
        "sure",
        "confirmed",
        "go ahead",
        "got it",
        "sounds good",
        # digits / length
        "hello 123",
        "I spent 40 SGD on lunch today at the hawker centre",
        # empty
        "",
        "   ",
    ]:
        assert not is_smalltalk_text(text), text


# ── is_smalltalk_turn shapes ─────────────────────────────────────────────────────


def test_turn_plain_string_content():
    assert is_smalltalk_turn(_turn("hello"))
    assert not is_smalltalk_turn(_turn("what's my net worth"))


def test_turn_screen_block_does_not_disqualify():
    content = [
        {"type": "text", "text": "hello"},
        {"type": "text", "text": _SCREEN_BLOCK},
    ]
    assert is_smalltalk_turn(_turn(content))


def test_turn_mention_context_disqualifies():
    content = [
        {"type": "text", "text": "hello"},
        {"type": "text", "text": "Mentioned items:\n- holding ONWAI: SGD 50,000"},
    ]
    assert not is_smalltalk_turn(_turn(content))


def test_turn_attachment_disqualifies():
    content = [
        {"type": "text", "text": "hello"},
        {"type": "image", "source": {"type": "base64", "data": "…"}},
    ]
    assert not is_smalltalk_turn(_turn(content))


def test_turn_non_user_or_empty_disqualifies():
    assert not is_smalltalk_turn([])
    assert not is_smalltalk_turn([{"role": "assistant", "content": "hello"}])
    assert not is_smalltalk_turn(_turn([]))


def test_turn_classifies_last_message_only():
    msgs = [
        {"role": "user", "content": "what's my net worth?"},
        {"role": "assistant", "content": "SGD 422,000."},
        {"role": "user", "content": "thanks"},
    ]
    assert is_smalltalk_turn(msgs)


# ── _resolve_style wiring ────────────────────────────────────────────────────────


def test_smalltalk_turn_gets_greeting_guard():
    assert _resolve_style(_turn("hello")) == SMALLTALK_STYLE


def test_substantive_turn_gets_no_style_line():
    # Effort no longer shapes answer length — a normal turn emits no RESPONSE STYLE
    # line at all (the model decides length).
    assert _resolve_style(_turn("what's my net worth?")) == ""


def test_smalltalk_style_forbids_data_and_structure():
    n = " ".join(SMALLTALK_STYLE.lower().split())
    assert "no financial figures" in n
    assert "no lists" in n
    assert "no menu of suggested topics" in n
    assert "1-2 friendly sentences" in n


# ── looks_over_answered (the over-answer tripwire predicate) ─────────────────────


def test_over_answer_short_conversational_replies_pass():
    for reply in [
        "Hey! What can I do for you?",
        "Doing great — what's on your mind?",
        "Anytime.",
        "",
        "   ",
    ]:
        assert not looks_over_answered(reply), reply


def test_over_answer_word_count_trips():
    assert not looks_over_answered("word " * OVER_ANSWER_WORD_CAP)
    assert looks_over_answered("word " * (OVER_ANSWER_WORD_CAP + 1))


def test_over_answer_structure_trips_even_when_short():
    for reply in [
        "Here's what's on the table:\n- Tax-loss harvest\n- FIRE progress",
        "Sure!\n* rebalancing\n* the apartment plan",
        "# Tax Summary\nAll good.",
        "Options:\n• trim ONWAI",
    ]:
        assert looks_over_answered(reply), reply


def test_over_answer_prose_with_inline_dash_does_not_trip():
    # A hyphenated aside mid-sentence is prose, not a bullet.
    assert not looks_over_answered("Doing well - thanks for asking! What's next?")
