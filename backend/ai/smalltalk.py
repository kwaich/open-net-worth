"""Deterministic smalltalk detection — the HARNESS half of the response-calibration
contract (ADR 2026-07-11-response-calibration-effort-as-ceiling).

The prompt contract asks the model to keep conversational replies short; this module
GUARANTEES it for the unambiguous cases: when the entire typed message is pure smalltalk
(a greeting / thanks / farewell / how-are-you), the dispatch loop swaps the per-turn
RESPONSE STYLE line for SMALLTALK_STYLE regardless of the effort dial. Safe by
construction — any substantive content disqualifies the turn (extra words fail the
lexicon, digits fail outright, @mention context / attachments / non-screen blocks fail
the turn-shape check), so a real request can never be down-styled. Fuzzy phrasings the
lexicon misses simply fall through to the prompt contract: defense in depth, not the
sole guard. No context is ever dropped — the model keeps full data access either way.

Deliberately EXCLUDED from the lexicon: affirmations/negations and acknowledgements
("yes", "no", "ok", "sure", "confirmed", "got it") — those are flow-control for
confirm-gated writes, where a figure-bearing reply ("Done — imported 28 rows") is
exactly right.
"""

from __future__ import annotations

import re

from backend.core.untrusted import is_screen_context_block

SMALLTALK_STYLE = (
    "This is a conversational message, not a data request. Reply in 1-2 friendly "
    "sentences. NO financial figures, NO lists, NO headers, NO menu of suggested "
    "topics; at most one short natural question back."
)

# Over-answer tripwire threshold (post-generation telemetry, not enforcement): a
# smalltalk reply under SMALLTALK_STYLE should be 1-2 sentences; 60 words is generous
# headroom above the behavioral eval's 45-word cap, so a warning means the contract
# genuinely failed, not that the model was mildly chatty.
OVER_ANSWER_WORD_CAP = 60

# Markdown bullets / headers at line start — structure has no place in a smalltalk reply.
_STRUCTURE_RE = re.compile(r"^\s*(?:[-*•]\s|#{1,6}\s)", re.MULTILINE)

# The typed message must be SHORT to qualify — anything longer carries substance.
_MAX_RAW_LEN = 40

# Screen context (core.untrusted.is_screen_context_block) rides every turn from an
# instrumented screen; its presence says nothing about the user's intent, so it never
# disqualifies. Any OTHER extra block (mention context, attachments) does.

_LEXICON = frozenset(
    {
        # greetings
        "hello",
        "hi",
        "hey",
        "yo",
        "hiya",
        "howdy",
        "sup",
        "whats up",
        "wassup",
        "good morning",
        "morning",
        "good afternoon",
        "afternoon",
        "good evening",
        "evening",
        "good day",
        "greetings",
        "hi there",
        "hey there",
        "hello there",
        "hello again",
        "hi again",
        "im back",
        # how-are-you
        "how are you",
        "how are you doing",
        "how are you today",
        "how is it going",
        "hows it going",
        "how goes it",
        "how are things",
        "hows things",
        "how do you do",
        "hows everything",
        "how is everything",
        "hows your day",
        "how was your day",
        "you good",
        "all good",
        # greeting + how-are-you combos
        "hello how are you",
        "hi how are you",
        "hey how are you",
        "good morning how are you",
        # thanks
        "thanks",
        "thank you",
        "thanks a lot",
        "thank you so much",
        "thanks so much",
        "thanks a bunch",
        "many thanks",
        "thx",
        "ty",
        "tysm",
        "cheers",
        "appreciate it",
        "much appreciated",
        # farewells
        "bye",
        "bye bye",
        "goodbye",
        "good night",
        "goodnight",
        "see you",
        "see ya",
        "see you later",
        "take care",
        "catch you later",
        "talk later",
        "talk to you later",
        "ttyl",
        "gotta go",
        # reactions / probes
        "lol",
        "haha",
        "hahaha",
        "hehe",
        "lmao",
        "wow",
        "test",
        "testing",
        "ping",
    }
)


def _normalize(raw: str) -> str:
    """Lowercase, drop apostrophes, replace non-alpha with spaces, drop 'lucius'
    tokens, collapse whitespace — so "Hey Lucius! 👋" and "What's up?" both hit the
    lexicon."""
    lowered = raw.lower().replace("'", "").replace("’", "")
    kept = "".join(ch if ch.isalpha() or ch.isspace() else " " for ch in lowered)
    tokens = [t for t in kept.split() if t != "lucius"]
    return " ".join(tokens)


def is_smalltalk_text(text: str) -> bool:
    """True iff the user's typed words are pure smalltalk (lexicon match)."""
    raw = (text or "").strip()
    if not raw or len(raw) > _MAX_RAW_LEN:
        return False
    if any(ch.isdigit() for ch in raw):
        return False
    return _normalize(raw) in _LEXICON


def looks_over_answered(reply: str, max_words: int = OVER_ANSWER_WORD_CAP) -> bool:
    """True iff a smalltalk reply broke the conversational contract — too long, or
    structured (bullets/headers). Pure predicate for the post-generation telemetry
    tripwire in the dispatch loop; it never alters behavior, only earns a log line
    that grows the eval's golden set from real failures."""
    text = (reply or "").strip()
    if not text:
        return False
    if len(text.split()) > max_words:
        return True
    return bool(_STRUCTURE_RE.search(text))


def is_smalltalk_turn(new_messages: list) -> bool:
    """True iff the turn's LAST message is a user message whose typed text is pure
    smalltalk and whose only extra content is the passive screen-context block.

    The typed words always LEAD the content array (api/chat.py merge contract); any
    non-text block (attachment) or non-screen text block (mention/skill context)
    signals substance and disqualifies."""
    if not new_messages:
        return False
    last = new_messages[-1]
    if not isinstance(last, dict) or last.get("role") != "user":
        return False
    content = last.get("content")
    if isinstance(content, str):
        return is_smalltalk_text(content)
    if not isinstance(content, list) or not content:
        return False
    first, rest = content[0], content[1:]
    if not isinstance(first, dict) or first.get("type") != "text":
        return False
    for block in rest:
        if not isinstance(block, dict) or block.get("type") != "text":
            return False
        if not is_screen_context_block(str(block.get("text", ""))):
            return False
    return is_smalltalk_text(str(first.get("text", "")))
