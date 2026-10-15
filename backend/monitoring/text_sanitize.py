"""Shared untrusted-text sanitization + tokenization for LLM-facing prompt content.

``sanitize_untrusted_text`` NFKC-normalizes FIRST (security L1: collapses lookalike/compatibility
characters — e.g. fullwidth "＞＞＞" or "－－－" — to their ASCII forms BEFORE the collapse passes
below run on them, so a lookalike can't smuggle a 3+ run past the collapse only to normalize back
into a real delimiter downstream), then strips ASCII control chars, then collapses dash-runs (which
could otherwise forge this repo's "--- BEGIN/END ... ---" data-block delimiters, letting untrusted
text escape its frame — see analyst.py's prompt-injection hardening) AND ">"-runs (INT12:
analyst.py's material_news trigger marker, ">>> NEW (this alert's trigger, published ...):", is OUR
OWN rendered prefix — collapsing any 3+ run of ">" means untrusted evidence text can never forge
that exact 3-char sequence to impersonate/duplicate the marker). Used anywhere user-authored or
third-party text rides a prompt: analyst.py/analyst_prompt.py (news snippets, investor-profile
notes, prior-signal-summary delta context), news_screen.py (headlines), and topic_analyst.py
(watchlist topic notes).

``tokenize``/``content_tokens`` are the ONE shared normalized-token-set implementation for the
same-story/same-cluster overlap checks used by both news_screen.py (clustering + the
cluster-fingerprint cooldown) and analyst_prompt.py (trigger/background dedup) — previously each
module carried its own near-identical `_TOKEN_RE`/`_tokens` copy.
"""

from __future__ import annotations

import re
import unicodedata

_CTRL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
_DASH_RE = re.compile(r"-{3,}")
_GT_RE = re.compile(r">{3,}")


def sanitize_untrusted_text(value: str | None, max_len: int) -> str:
    text = unicodedata.normalize("NFKC", value or "")
    text = _CTRL_RE.sub("", text)
    text = _DASH_RE.sub("-", text)
    text = _GT_RE.sub(">", text)
    return text[:max_len]


_TOKEN_RE = re.compile(r"[a-z0-9]+")

# A small, deliberately conservative stopword set for overlap comparisons (INT12 SF-2): filler
# words a headline's own grammar contributes regardless of what actually happened, so they
# shouldn't count toward "these are the same story." NOT a general-purpose NLP stopword list —
# just enough to stop trivial false-positives.
_OVERLAP_STOPWORDS: frozenset[str] = frozenset(
    {
        "a",
        "an",
        "the",
        "of",
        "in",
        "on",
        "at",
        "to",
        "for",
        "and",
        "or",
        "with",
        "from",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "that",
        "this",
        "it",
        "its",
        "as",
        "by",
        "not",
        "no",
        "vs",
        "new",
        "says",
        "said",
        "say",
        "report",
        "reports",
        "reported",
        "news",
        "amid",
        "over",
        "into",
        "after",
        "before",
        "up",
        "down",
        "out",
    }
)


def tokenize(text: str) -> set[str]:
    """Lowercased alnum-run token set, no stemming — the raw building block for overlap checks."""
    return set(_TOKEN_RE.findall((text or "").lower()))


def content_tokens(text: str, *, subject_name: str = "") -> set[str]:
    """``tokenize`` minus a small stopword set and the subject's own name tokens.

    Without this, two headlines that share only the subject's name + grammatical filler — e.g.
    "Apple Q3 miss" vs "Apple Q3 beat" — can hit a 0.6 overlap threshold on filler alone and be
    wrongly treated as the same story/cluster even though they report OPPOSITE developments.
    Accepted residual risk: this is a cheap heuristic, not semantic understanding — two SHORT
    headlines describing genuinely different specific developments can still occasionally share
    enough real content words to clear the threshold; the fix narrows the false-positive surface,
    it doesn't eliminate it.
    """
    toks = tokenize(text) - _OVERLAP_STOPWORDS
    if subject_name:
        toks -= tokenize(subject_name)
    return toks
