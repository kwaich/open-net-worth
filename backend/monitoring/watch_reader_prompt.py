"""Lucius's read on a watch story — prompt, schema and checks. Pure: no I/O, no model call.

Where the explained alert (``watch_analyst_prompt``) answers "why did it just move" for a
material trigger, the read answers "what does this mean for someone watching it" for every live
watch story — earnings in a week, a 200-day cross, a big but ordinary move. It is handed the same
closed FACTS, plus today's price-movement note and the asset brief's earlier research when they
exist, and must stay within the user's opinion level (``ai_opinion``) and use only those figures.
"""

from __future__ import annotations

from backend.monitoring import ai_opinion
from backend.monitoring.watch_analyst_prompt import invents_figures

NOTE_MAX_CHARS = 500
RESEARCH_MAX_ITEMS = 3
RESEARCH_MAX_CHARS = 220

_SCHEMA = {
    "type": "object",
    "properties": {
        "read": {"type": "string"},
        "watch_for": {"type": "array", "items": {"type": "string"}},
        "source_ids": {"type": "array", "items": {"type": "integer"}},
    },
    "required": ["read", "watch_for"],
}

SYSTEM = (
    "You write a short read on a stock the user WATCHES but does not own, for a personal-finance "
    "app: what today's signals mean for someone tracking it, given their own thesis and buy "
    "zone. Use ONLY the figures in FACTS and the text in NOTE and EARLIER RESEARCH — never invent "
    "a number, a cause or a date. Plain words, no jargon."
)


def schema_for(level: str) -> dict:
    return {
        **_SCHEMA,
        "properties": {**_SCHEMA["properties"], **ai_opinion.schema_properties(level)},
    }


def system_for(level: str) -> str:
    return f"{SYSTEM} {ai_opinion.clause(level)}"


def _issue_line(issue: object) -> str:
    """One key issue as text, whichever shape the brief stored it in."""
    if not isinstance(issue, dict):
        return str(issue or "")

    def side(v: object) -> str:
        return str(v.get("text") or "") if isinstance(v, dict) else str(v or "")

    parts = [str(issue.get("question") or "")]
    if side(issue.get("bullish")):
        parts.append(f"bull: {side(issue.get('bullish'))}")
    if side(issue.get("bearish")):
        parts.append(f"bear: {side(issue.get('bearish'))}")
    return " — ".join(p for p in parts if p)


def research_lines(brief: dict | None) -> list[str]:
    """The asset brief's key issues, capped — earlier model research on the stock."""
    issues = (brief or {}).get("key_issues") or []
    lines = [_issue_line(i)[:RESEARCH_MAX_CHARS] for i in issues[:RESEARCH_MAX_ITEMS]]
    return [line for line in lines if line.strip()]


def grounded_text(note: dict | None, research: list[str]) -> str:
    """Text whose figures count as grounded: the note, its source titles and the research."""
    note = note or {}
    titles = [str(s.get("title") or "") for s in note.get("sources") or []]
    return " ".join([str(note.get("summary") or ""), *titles, *research])


def build_prompt(
    headline: str, facts: dict, note: dict | None, research: list[str], level: str
) -> str:
    sources = (note or {}).get("sources") or []
    listed = "\n".join(
        f"[{i}] {s.get('title') or s.get('domain') or s.get('url')}" for i, s in enumerate(sources)
    )
    note_block = (
        f"NOTE (today's sourced 'why it moved'):\n{str(note['summary'])[:NOTE_MAX_CHARS]}\n\n"
        f"SOURCES:\n{listed}\n\n"
        if note and note.get("summary")
        else ""
    )
    research_block = (
        "EARLIER RESEARCH (key questions on this stock):\n"
        + "\n".join(f"- {line}" for line in research)
        + "\n\n"
        if research
        else ""
    )
    return (
        f"STORY: {headline}\n\n"
        f"FACTS (the only figures you may use):\n{facts}\n\n"
        f"{note_block}{research_block}"
        "Return JSON with:\n"
        "- read: one or two sentences on what this means for someone watching the stock — tie "
        "it to their thesis, buy zone and fair value (the FACTS starting with 'your_') when "
        "they exist.\n"
        "- watch_for: one or two short things worth watching next.\n"
        "- source_ids: the [n] SOURCES numbers your read relies on, or [] if none.\n"
        + ai_opinion.instructions(level)
    )


def check_read(
    result: object, facts: dict, n_sources: int, grounded: str, level: str
) -> str | None:
    """The validator handed to ``complete_json``: an error string makes it reask once."""
    if not isinstance(result, dict):
        return "Return a JSON object."
    if not isinstance(result.get("read"), str) or not result["read"].strip():
        return "'read' must be a non-empty string."
    watch = result.get("watch_for")
    if not isinstance(watch, list) or not 1 <= len(watch) <= 3:
        return "'watch_for' must list one or two short things to watch."
    ids = result.get("source_ids") or []
    if not isinstance(ids, list) or any(
        not isinstance(i, int) or not 0 <= i < n_sources for i in ids
    ):
        return f"'source_ids' must be SOURCES numbers between 0 and {max(n_sources - 1, 0)}, or []."
    text = " ".join([result["read"], *map(str, watch), *ai_opinion.texts(result, level)])
    overstep = ai_opinion.error(result, text, level)
    if overstep:
        return overstep
    bad = invents_figures(text, facts, grounded)
    if bad:
        return f"The number {bad} is not in FACTS, NOTE or EARLIER RESEARCH. Use only those."
    return None
