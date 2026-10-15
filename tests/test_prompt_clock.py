"""The prompt has to carry the TIME, not just the date.

Asked "what time is it now?" at 03:00 Singapore, Lucius answered "it's currently evening
in Singapore" — it had TODAY'S DATE and nothing finer, so it guessed the hour. Anything
scheduled, overdue, "by end of day" or "this morning" was being reasoned about from a
guess.
"""

from __future__ import annotations

import re

from backend.ai.prompts import CACHE_SPLIT_MARKER, TURN_SPLIT_MARKER, build_system_prompt


def _date_line() -> str:
    prompt = build_system_prompt()
    return next(line for line in prompt.split("\n") if line.startswith("TODAY'S DATE:"))


def test_the_prompt_states_the_clock_time_and_the_zone():
    # e.g. "TODAY'S DATE: 2026-08-03 — Monday 03:36 +08 (UTC+0800)"
    assert re.search(r"\d{2}:\d{2}", _date_line())
    assert "UTC" in _date_line()


def test_the_weekday_is_named_rather_than_left_to_be_derived():
    assert any(
        day in _date_line()
        for day in ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
    )


def test_the_clock_rides_the_PER_TURN_block_so_it_cannot_bust_the_cache():
    """A value that changes every minute in the CACHED prefix would re-write the whole
    conversation prefix on every turn. The per-turn tail is injected into the user message,
    so it costs nothing to change."""
    prompt = build_system_prompt()
    per_turn = prompt.split(TURN_SPLIT_MARKER)[-1]
    cached_prefix = prompt.split(CACHE_SPLIT_MARKER)[0]

    assert "TODAY'S DATE:" in per_turn
    assert re.search(r"\d{2}:\d{2}", cached_prefix) is None


def test_the_cached_prefix_is_stable_across_calls():
    """Belt and braces on the above: two builds a moment apart must not differ before the
    cache split, or every turn pays a full prefix rewrite."""
    first = build_system_prompt().split(CACHE_SPLIT_MARKER)[0]
    second = build_system_prompt().split(CACHE_SPLIT_MARKER)[0]

    assert first == second
