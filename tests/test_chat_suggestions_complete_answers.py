"""Follow-up suggestion chips must be complete, sendable statements — and must cover a
compound question in one reply, not split it across several buttons. They also must never
invent the client's own name to fill in an answer (guessing elsewhere stays fine).

The bug (live, 2026-08-25): asked "what should I call you?", the chips included "My name
is…" — an unfinished sentence fragment. Chips send their text VERBATIM the instant they're
tapped, with no composer step to complete them in — so a fragment can never actually be
sent as a real answer. It only existed because nothing in the prompt forbade it.

Separately, a compound question ("what's your nationality, and are you tax resident
there?") needs ONE suggestion that answers both halves, not one button per half — the
button sends whatever it holds in a single message; splitting the question across buttons
would only ever let the client answer part of it per tap.

A third, related bug (2026-08-25): fixing the fragment bug above by requiring a COMPLETE
answer risks the model completing an unknowable template with a GUESS — e.g. "My name is
Alex" when the client's real name was never stated. That is worse than a fragment: tapping
it sends false information as fact. The fix is scoped narrowly to the client's own name —
there is no small, sensible set of plausible names to choose from, unlike a question with
a small enumerable answer set (gender, marital status, a yes/no), where proposing one
plausible answer remains fine.

Two copies of this system prompt exist by design (module docstrings explain why: the web
surface lives behind ``api``, the connector-reachable twin lives in ``ai`` since connectors
may not import ``api``) and are "kept semantically in sync by hand" — so both get pinned.
"""

from __future__ import annotations

from backend.ai.chat_suggestions import _FOLLOWUP_SYSTEM as AI_FOLLOWUP_SYSTEM
from backend.api.chat_suggestions import _FOLLOWUP_SYSTEM as API_FOLLOWUP_SYSTEM


def test_api_surface_forbids_incomplete_suggestions():
    assert "COMPLETE" in API_FOLLOWUP_SYSTEM
    assert "sentence fragment" in API_FOLLOWUP_SYSTEM
    assert "no chance to edit first" in API_FOLLOWUP_SYSTEM


def test_connector_surface_forbids_incomplete_suggestions():
    assert "COMPLETE" in AI_FOLLOWUP_SYSTEM
    assert "sentence fragment" in AI_FOLLOWUP_SYSTEM
    assert "no chance to edit first" in AI_FOLLOWUP_SYSTEM


def test_api_surface_covers_a_compound_question_in_one_reply():
    assert "more than one thing at once" in API_FOLLOWUP_SYSTEM
    assert "never split one compound question" in API_FOLLOWUP_SYSTEM


def test_connector_surface_covers_a_compound_question_in_one_reply():
    assert "more than one thing at once" in AI_FOLLOWUP_SYSTEM
    assert "never split one compound question" in AI_FOLLOWUP_SYSTEM


def test_api_surface_forbids_guessing_the_clients_name():
    assert "OWN NAME" in API_FOLLOWUP_SYSTEM
    assert "My name is Alex" in API_FOLLOWUP_SYSTEM
    # Narrower than "never guess any personal fact" — a small enumerable answer
    # (gender, marital status, a yes/no) is still fine to propose.
    assert "stays fair game to guess" in API_FOLLOWUP_SYSTEM


def test_connector_surface_forbids_guessing_the_clients_name():
    assert "OWN NAME" in AI_FOLLOWUP_SYSTEM
    assert "My name is Alex" in AI_FOLLOWUP_SYSTEM
    assert "stays fair game to guess" in AI_FOLLOWUP_SYSTEM


def test_neither_surface_guesses_amounts_but_may_name_a_local_bank():
    """Persona QA 2026-10-13: "Balance is about S$1,200" was offered to a client who owed
    S$540 — one tap submits a made-up figure. A bank name is a shortcut, not a fact: a
    wrong one is simply not tapped, so it stays allowed as long as it is local (a
    Singapore client was offered Chase / Bank of America / Wells Fargo)."""
    for prompt in (API_FOLLOWUP_SYSTEM, AI_FOLLOWUP_SYSTEM):
        assert "any amount" in prompt
        assert "fits where the" in prompt


def test_the_followup_prompt_says_where_the_client_is(monkeypatch):
    from backend.ai import chat_suggestions

    monkeypatch.setattr("backend.core.fx.base_currency", lambda: "SGD")
    assert chat_suggestions.home_currency_line() == "The client's home currency is SGD."
