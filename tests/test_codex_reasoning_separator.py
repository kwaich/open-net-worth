"""Two Codex reasoning summaries must not run into each other.

Codex delivers each summary as a COMPLETE item wrapped in `**…**` (`item.completed` with
`item_type: reasoning`), not as a character stream. Concatenated with no separator, two of
them meet as `**Planning BTC info retrieval****Planning credible finance source use**` —
which markdown cannot parse as two bold runs, so the reader sees the raw asterisks instead
of two headings (owner report, 2026-08-23, gpt-5.5 on the demo instance).

The legacy `agent_reasoning_delta` path is character-level and must NOT be separated, or
blank lines land mid-sentence.
"""

from __future__ import annotations

from backend.ai.models.codex_cli_provider import _classify, _thinking_text


def test_a_whole_item_is_separated_from_the_one_before_it():
    a = {"text": "**Planning BTC info retrieval**", "whole": True}
    b = {"text": "**Planning credible finance source use**", "whole": True}
    joined = _thinking_text(a, False) + _thinking_text(b, True)
    assert "****" not in joined
    assert joined == "**Planning BTC info retrieval**\n\n**Planning credible finance source use**"


def test_character_deltas_are_joined_untouched():
    d1 = {"text": "Think", "whole": False}
    d2 = {"text": "ing about it.", "whole": False}
    assert _thinking_text(d1, False) + _thinking_text(d2, True) == "Thinking about it."


def test_the_first_item_is_never_prefixed():
    a = {"text": "**Only thought**", "whole": True}
    assert _thinking_text(a, False) == "**Only thought**"


def test_classify_marks_a_completed_reasoning_item_as_whole():
    ev = {
        "type": "item.completed",
        "item": {"item_type": "reasoning", "text": "**Deciding**"},
    }
    parsed = _classify(ev)
    assert parsed["kind"] == "thinking"
    assert parsed.get("whole") is True


def test_classify_does_not_mark_a_reasoning_DELTA_as_whole():
    ev = {"msg": {"type": "agent_reasoning_delta", "delta": "part of a sentence"}}
    parsed = _classify(ev)
    assert parsed["kind"] == "thinking"
    assert not parsed.get("whole")
