"""`__ONBOARDING_INIT__` must never surface to a human.

The bug (found by the owner on a live instance, 2026-08-22): the sentinel `ChatPanel` sends to
make Lucius speak first was rendered as the owner's own chat bubble AND stored as the
conversation's TITLE — a session row literally named `__ONBOARDING_INIT__`.

`sendMessage(..., silent=true)` correctly kept it out of the LOCAL optimistic state, which is
why it looked fine in unit tests. It leaked through the SERVER path instead:
`db/session_io._recovered_title` names a conversation after its first user-role message, and the
sentinel was not recognised as harness-authored.

This is the same defect class as the 2026-08-21 proactive-alert leak, which needed a one-off
repair script (`backend/scripts/retitle_harness_note_sessions.py`) after a real Telegram session
was titled "[Automated monitor — the following message was delivered to". The fix is the same
mechanism: the sentinel joins the legacy bare harness-authored openings, so every reader that
already knows to skip those skips it too.

Kept BARE rather than wrapped in «harness_note»: `ai/prompts.py` and `ai/assistant.py` both match
the sentinel by exact equality, so wrapping it would silently break the greeting it triggers.
"""

from __future__ import annotations

from backend.ai.tool_text import is_harness_note
from backend.db.session_io import _is_harness_authored, _recovered_title, _user_message_text

SENTINEL = "__ONBOARDING_INIT__"


def test_both_recognisers_call_the_sentinel_harness_authored():
    """Deliberately duplicated implementations (see session_io's module docstring) — a fix to
    one that misses the other is exactly how this leaked in the first place."""
    assert is_harness_note(SENTINEL) is True
    assert _is_harness_authored(SENTINEL) is True


def test_the_sentinel_contributes_no_title_text():
    assert _user_message_text(SENTINEL) == ""


def test_a_conversation_started_by_the_sentinel_is_not_named_after_it():
    """Falls back to the generic label — the same one a transcript with no real user message
    already produced. What matters is that the magic token is not the name."""
    title = _recovered_title([{"role": "user", "content": SENTINEL}])
    assert SENTINEL not in title
    assert title == "Recovered conversation"


def test_the_first_REAL_message_still_names_the_conversation():
    """The fix must not make onboarding conversations permanently unnamed — once the user
    actually says something, that is the title."""
    title = _recovered_title(
        [
            {"role": "user", "content": SENTINEL},
            {"role": "assistant", "content": "Hi — what should I call you?"},
            {"role": "user", "content": "Alex, but call me A"},
        ]
    )
    assert title == "Alex, but call me A"


def test_ordinary_prose_is_untouched():
    """The recogniser matches the sentinel exactly, never a loose prefix — a person who types
    something merely similar must not be silenced."""
    assert is_harness_note("__ONBOARDING_INIT__ is what my app sends") is False
    assert _user_message_text("what does __ONBOARDING_INIT__ do?") == (
        "what does __ONBOARDING_INIT__ do?"
    )
