"""The model must not ask a question and act in the same breath.

Owner report 2026-08-24: Lucius wrote "I'll revert the category back to unset — confirm?"
and then called `update_transactions` in the same response, so the write landed before the
question could be answered. The permission gate was working exactly as configured
(`update_transactions` is `always_allow` on that instance) — the defect is that the model
asked a question it had no intention of waiting for.

A question the user cannot answer before you act is narration, and it teaches them that
their answer does not matter. This pins the rule in both prompts, because the same behaviour
shows up in an agent run as well as a chat turn.
"""

from __future__ import annotations

import backend.ai.tools  # noqa: F401  (populates the tool registry the prompts render)
from backend.ai.prompts import build_lean_system_prompt, build_system_prompt


def _text(prompt) -> str:
    return prompt if isinstance(prompt, str) else "".join(str(part) for part in prompt)


def test_the_chat_prompt_states_that_asking_ends_the_turn():
    text = _text(build_system_prompt())
    assert "ASKING ENDS THE TURN" in text
    assert "do not call a tool in the same response" in text


def test_the_agent_prompt_states_it_too():
    assert "ASKING ENDS THE TURN" in _text(build_lean_system_prompt(None))


def test_the_two_prompts_state_the_same_rule():
    """``ASKING ENDS THE TURN`` is the ONE rule written into both prompts, and they are
    separate prose that has already been edited in parallel — the agent prompt kept a stale
    confirm-flow block for a month after the chat one was fixed (2026-08-25).

    A shared constant would be over-engineering for a single rule, so the drift is caught
    here instead: both must state the same substance, however each is worded and wrapped."""
    chat = " ".join(_text(build_system_prompt()).split()).lower()
    agent = " ".join(_text(build_lean_system_prompt(None)).split()).lower()
    for clause in (
        "that question is your turn",
        "do not call a tool in the same response",
        "it is narration",
        "a call fires the moment you make it",
    ):
        assert clause in chat, f"chat prompt lost: {clause!r}"
        assert clause in agent, f"agent prompt lost: {clause!r}"


def test_it_says_prose_does_not_create_a_pause():
    """The other half of the confusion: asking is not a gate — a call fires when you make it.

    It used to say so by explaining the PERMISSION LEVEL. That explanation is gone, and
    deliberately: whether a call is gated is the harness's decision, taken from a stored
    level the model cannot see, and it differs per tool — ``always_allow`` shows no card at
    all. Telling the model about cards therefore describes something untrue half the time,
    and invites it to predict a gate instead of just calling the tool. What survives is the
    only part that is always true and always the model's business: your calls are real."""
    text = _text(build_system_prompt()).lower()
    assert "asking creates no pause" in text
    assert "fires the moment you make it" in text
    # And nothing that teaches it to reason about the gate.
    for gone in ("permission level", "confirm card", "staged call"):
        assert gone not in text, f"the prompt is explaining the gate again: {gone!r}"
