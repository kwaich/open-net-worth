"""The daily briefing always carries a standing instruction.

The briefing widget's prompt box never reached the server (it sent a chat message instead), so
the only instruction the briefing honoured was ``briefing_preferences.custom_focus`` — empty by
default, i.e. an unguided three-paragraph note. An empty focus now falls back to a sensible
default, and a user's own focus is kept verbatim.
"""

from __future__ import annotations

from backend.api import briefing
from backend.models.profile import DEFAULT_BRIEFING_FOCUS
from backend.services import profile as profile_service


def _set_focus(text: str) -> None:
    current = profile_service.read_profile_raw()
    prefs = dict(current.get("briefing_preferences") or {})
    prefs["custom_focus"] = text
    profile_service.write_profile({**current, "briefing_preferences": prefs})


def test_an_empty_focus_reads_as_the_default(db_isolated):
    _set_focus("")
    assert profile_service.read_profile()["briefing_preferences"]["custom_focus"] == (
        DEFAULT_BRIEFING_FOCUS
    )


def test_the_users_own_focus_is_kept(db_isolated):
    _set_focus("Only tell me about my mortgage.")
    prefs = profile_service.read_profile()["briefing_preferences"]
    assert prefs["custom_focus"] == "Only tell me about my mortgage."


def test_the_briefing_context_carries_the_focus(db_isolated):
    _set_focus("")
    context = briefing._build_context(0.0)
    assert "STANDING INSTRUCTIONS FOR THIS BRIEFING" in context
    assert DEFAULT_BRIEFING_FOCUS in context
