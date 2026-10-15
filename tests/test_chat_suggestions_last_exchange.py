"""``_last_exchange`` (and its ``_real_client_text`` helper) in both copies of
chat_suggestions.py — the follow-up suggestion chips' "what did the client last ask"
extractor.

The bug (live, 2026-08-25): on a brand-new onboarding session Lucius speaks first, but
the Messages API requires the transcript to START with a ``role: "user"`` turn, so the
app satisfies that with hidden, harness-authored entries — a bare ``__ONBOARDING_INIT__``
block immediately followed by a ``<turn_context>`` block, and a SEPARATE skill-invocation
entry (marked ``_onw_skill``) carrying the entire onboarding-new-user skill body. Neither
is the client's own words. Before this fix, ``_last_exchange`` picked up the skill body
(or, once that was fixed, the merged onboarding-init+turn-context text) and handed it to
the suggestion model framed as "The client asked: [...]" — which produced fabricated
name suggestions ("My name is Alex") once asked to propose answers to that turn.

Root cause detail: ``_block_text`` flattens EVERY content block (turn_context included)
into one string BEFORE any filtering runs, so a bare ``__ONBOARDING_INIT__`` block merged
with its trailing ``<turn_context>`` block no longer string-matches
``is_harness_note``'s exact-equality check. ``_real_client_text`` fixes this by filtering
block by block, mirroring ``ai/assistant.py``'s own (module-private) transcript
reconstruction filtering, THEN joining.

Two copies of this system exist by design (module docstrings explain why: the web surface
lives behind ``api``, the connector-reachable twin lives in ``ai`` since connectors may
not import ``api``) — both get pinned.
"""

from __future__ import annotations

from backend.ai.chat_suggestions import _last_exchange as ai_last_exchange
from backend.ai.chat_suggestions import _real_client_text as ai_real_client_text
from backend.api.chat_suggestions import _last_exchange as api_last_exchange
from backend.api.chat_suggestions import _real_client_text as api_real_client_text

# The exact turn-0 shape a brand-new onboarding session produces: a two-block
# __ONBOARDING_INIT__ + <turn_context> user entry, a separate skill-invocation entry
# (marked _onw_skill), then Lucius's real welcome message.
ONBOARDING_HISTORY = [
    {
        "role": "user",
        "content": [
            {"type": "text", "text": "__ONBOARDING_INIT__"},
            {
                "type": "text",
                "text": "<turn_context>\nTODAY'S DATE: 2026-08-25\n</turn_context>",
            },
        ],
    },
    {
        "role": "user",
        "content": "[SKILL INVOCATION: onboarding-new-user (onboarding-new-user)]\n---\n"
        "name: onboarding-new-user\n...(the whole skill body)...\nARGUMENTS: __ONBOARDING_INIT__\n",
        "_onw_skill": {
            "skill_id": "onboarding-new-user",
            "skill_name": "onboarding-new-user",
            "source": "model",
        },
    },
    {
        "role": "assistant",
        "content": "Welcome — I'm Lucius. And who do I have the pleasure of speaking with?",
    },
]


def test_api_surface_never_extracts_the_onboarding_trigger_as_client_text():
    exchange = api_last_exchange(ONBOARDING_HISTORY)
    assert exchange is not None
    user_q, assistant_a = exchange
    assert user_q == ""
    assert "SKILL INVOCATION" not in user_q
    assert "ONBOARDING_INIT" not in user_q
    assert assistant_a.startswith("Welcome — I'm Lucius")


def test_connector_surface_never_extracts_the_onboarding_trigger_as_client_text():
    exchange = ai_last_exchange(ONBOARDING_HISTORY)
    assert exchange is not None
    user_q, assistant_a = exchange
    assert user_q == ""
    assert "SKILL INVOCATION" not in user_q
    assert "ONBOARDING_INIT" not in user_q
    assert assistant_a.startswith("Welcome — I'm Lucius")


def test_api_surface_still_finds_a_real_prior_message_behind_a_skill_invocation():
    """A skill invocation mid-conversation must not swallow a REAL user message that
    sits behind it — only the invocation entry itself is skipped."""
    history = [
        {"role": "user", "content": "I hold some Apple stock, can you help me add it?"},
        {
            "role": "user",
            "content": "[SKILL INVOCATION: add-holding (add-holding)]\n...",
            "_onw_skill": {"skill_id": "add-holding", "source": "model"},
        },
        {"role": "assistant", "content": "Sure — what quantity, and at which broker?"},
    ]
    exchange = api_last_exchange(history)
    assert exchange == (
        "I hold some Apple stock, can you help me add it?",
        "Sure — what quantity, and at which broker?",
    )


def test_connector_surface_still_finds_a_real_prior_message_behind_a_skill_invocation():
    history = [
        {"role": "user", "content": "I hold some Apple stock, can you help me add it?"},
        {
            "role": "user",
            "content": "[SKILL INVOCATION: add-holding (add-holding)]\n...",
            "_onw_skill": {"skill_id": "add-holding", "source": "model"},
        },
        {"role": "assistant", "content": "Sure — what quantity, and at which broker?"},
    ]
    exchange = ai_last_exchange(history)
    assert exchange == (
        "I hold some Apple stock, can you help me add it?",
        "Sure — what quantity, and at which broker?",
    )


def test_api_surface_ordinary_exchange_is_unaffected():
    """Regression guard: the common case (no onboarding weirdness) is unchanged."""
    history = [
        {"role": "user", "content": "What's my net worth?"},
        {"role": "assistant", "content": "SGD 245,000 as of today."},
    ]
    assert api_last_exchange(history) == ("What's my net worth?", "SGD 245,000 as of today.")


def test_connector_surface_ordinary_exchange_is_unaffected():
    history = [
        {"role": "user", "content": "What's my net worth?"},
        {"role": "assistant", "content": "SGD 245,000 as of today."},
    ]
    assert ai_last_exchange(history) == ("What's my net worth?", "SGD 245,000 as of today.")


def test_api_real_client_text_strips_a_merged_onboarding_init_and_turn_context():
    content = [
        {"type": "text", "text": "__ONBOARDING_INIT__"},
        {"type": "text", "text": "<turn_context>\nsome snapshot\n</turn_context>"},
    ]
    assert api_real_client_text(content) == ""


def test_connector_real_client_text_strips_a_merged_onboarding_init_and_turn_context():
    content = [
        {"type": "text", "text": "__ONBOARDING_INIT__"},
        {"type": "text", "text": "<turn_context>\nsome snapshot\n</turn_context>"},
    ]
    assert ai_real_client_text(content) == ""


def test_api_real_client_text_keeps_real_text_alongside_turn_context():
    content = [
        {"type": "text", "text": "What's my net worth?"},
        {"type": "text", "text": "<turn_context>\nsome snapshot\n</turn_context>"},
    ]
    assert api_real_client_text(content) == "What's my net worth?"


def test_connector_real_client_text_keeps_real_text_alongside_turn_context():
    content = [
        {"type": "text", "text": "What's my net worth?"},
        {"type": "text", "text": "<turn_context>\nsome snapshot\n</turn_context>"},
    ]
    assert ai_real_client_text(content) == "What's my net worth?"
