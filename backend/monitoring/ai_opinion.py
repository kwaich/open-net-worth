"""How far the AI's opinion on watched tickers may go — the user's ``ai_opinion_level`` setting
(Settings › Intelligence; ADR 2026-09-27-watch-story-reads-and-opinion-level). Pure: no I/O.

Three levels, each one a prompt clause, the extra output fields and a checker:

* ``information`` (default) — interpretation and considerations only; any instruction or
  verdict ("you should", "buy now") is rejected, exactly as the explained alert always did.
* ``lean`` — adds a labelled ``lean`` (bullish / bearish / neutral) with a one-line reason.
  Instructions are still rejected.
* ``suggest`` — adds at most one ``suggestion`` framed against the user's own thesis, buy zone
  and fair value. Only urgency or certainty is rejected ("buy now", "must sell", "guaranteed").

Every level keeps the figure check: whatever the level, a number must come from the facts.
"""

from __future__ import annotations

import re

LEVELS = ("information", "lean", "suggest")
LEANS = ("bullish", "bearish", "neutral")
DEFAULT_LEVEL = "information"

# Phrases that turn information into an instruction. Case-insensitive, whole words.
VERDICT = re.compile(
    r"\b(you should|we recommend|i recommend|recommend (?:buying|selling)|buy now|sell now|"
    r"strong buy|strong sell|must (?:buy|sell)|go long|go short|time to (?:buy|sell)|"
    r"a (?:clear )?buy signal|a (?:clear )?sell signal)\b",
    re.IGNORECASE,
)
# What even `suggest` never allows: urgency and certainty.
COMMAND = re.compile(
    r"\b(buy now|sell now|must (?:buy|sell|add|trim)|strong buy|strong sell|guaranteed|"
    r"can(?:no|')t lose|all[- ]in|act now|before it's too late)\b",
    re.IGNORECASE,
)

_CLAUSES = {
    "information": (
        "Never tell the user to buy or sell and never say what they 'should' do: give "
        "information and considerations; the decision is theirs."
    ),
    "lean": (
        "Never tell the user to buy or sell and never say what they 'should' do. You MAY give "
        "your own overall lean on the stock from these facts — bullish, bearish or neutral — "
        "with a one-sentence reason grounded in them; it is labelled as your view."
    ),
    "suggest": (
        "You MAY give your own overall lean — bullish, bearish or neutral — with a one-sentence "
        "reason, and AT MOST ONE suggestion framed against the user's OWN thesis, buy zone and "
        "fair value (e.g. 'Consider adding only inside your buy zone, since…'). Never urge "
        "('buy now', 'must sell'), never promise an outcome; it is labelled as your view and "
        "the decision is theirs."
    ),
}


def level_of(config: dict | None) -> str:
    level = (config or {}).get("ai_opinion_level")
    return level if level in LEVELS else DEFAULT_LEVEL


def clause(level: str) -> str:
    return _CLAUSES.get(level, _CLAUSES[DEFAULT_LEVEL])


def schema_properties(level: str) -> dict:
    """The JSON-schema properties this level adds to a response."""
    props: dict = {}
    if level in ("lean", "suggest"):
        props["lean"] = {"type": "string", "enum": list(LEANS)}
        props["lean_reason"] = {"type": "string"}
    if level == "suggest":
        props["suggestion"] = {"type": "string"}
    return props


def instructions(level: str) -> str:
    """The lines this level adds to a prompt's "Return JSON with:" list."""
    lines = ""
    if level in ("lean", "suggest"):
        lines += (
            "- lean: your overall lean from these facts — bullish, bearish or neutral.\n"
            "- lean_reason: one sentence, grounded in the facts, on why.\n"
        )
    if level == "suggest":
        lines += (
            "- suggestion: at most one short suggestion framed against the user's own thesis, "
            "buy zone or fair value — or an empty string when there is nothing to add.\n"
        )
    return lines


def texts(result: dict, level: str) -> list[str]:
    """The opinion strings of a response, so the caller's figure check covers them too."""
    return [str(result.get(k) or "") for k in fields_for(level) if k != "lean"]


def fields_for(level: str) -> tuple[str, ...]:
    if level == "suggest":
        return ("lean", "lean_reason", "suggestion")
    if level == "lean":
        return ("lean", "lean_reason")
    return ()


def error(result: dict, text: str, level: str) -> str | None:
    """Why a response oversteps ``level`` — an error string the model is reasked with — else None.
    ``text`` is every user-facing string of the response, opinion fields included."""
    if level in ("lean", "suggest"):
        if result.get("lean") not in LEANS:
            return "'lean' must be one of bullish, bearish or neutral."
        if not str(result.get("lean_reason") or "").strip():
            return "'lean_reason' must be one sentence grounded in the facts."
    pattern = COMMAND if level == "suggest" else VERDICT
    hit = pattern.search(text)
    if hit:
        if level == "suggest":
            return f"Don't urge or promise ('{hit.group(0)}'); frame it as a consideration."
        return f"Don't tell the user what to do ('{hit.group(0)}'); give considerations."
    return None


def persisted(result: dict, level: str) -> dict:
    """The opinion fields to store beside the write-up (empty at ``information``)."""
    out: dict = {}
    for key in fields_for(level):
        value = str(result.get(key) or "").strip()
        if value:
            out[key] = value
    return out


def view_line(fields: dict) -> str:
    """One plain line for a push message, e.g. "Lucius's view: leans bearish — …"."""
    parts: list[str] = []
    if fields.get("lean"):
        reason = f" — {fields['lean_reason']}" if fields.get("lean_reason") else ""
        parts.append(f"leans {fields['lean']}{reason}")
    if fields.get("suggestion"):
        parts.append(fields["suggestion"])
    return f"Lucius's view: {'; '.join(parts)}" if parts else ""
