"""Adaptive model selection — pure heuristic, no network, no LLM calls.

route() classifies a single user turn as "weak" (cheap fast model) or "strong"
(instance-default model).  It is conversation-sticky: once a session escalates to
strong it stays strong for the rest of the conversation, because Anthropic prompt
caches are model-scoped and a mid-conversation flip-flop pays two cold cache writes.

Rules (evaluated in order, first match wins):
  1. prior_route == "strong"  →  strong (sticky escalation, never de-escalate)
  2. has_attachments           →  strong (vision / parsing tasks)
  3. message length > 350 chars →  strong (complex ask implied by verbosity)
  4. strong-intent keywords    →  strong  (see _STRONG_RE)
  5. fallthrough               →  weak

Empty / whitespace-only input → strong (quality-safe default).
"""

from __future__ import annotations

import re

# Compiled once at module load — route() is called every interactive turn.
# Stem patterns (reconcil*, rebalanc*, simulat*, migrat*) use \b at the START
# only — the end of a stem is inside the word so a trailing \b would prevent
# "reconcile", "rebalancing", etc. from matching.
_STRONG_RE = re.compile(
    r"""
    # Statement / import / reconcile / rollback
    (?:\bstatement\b|\bimport\b|\breconcil\w*|\brollback\b|\broll\s+back\b)
    # Merge / dedup / matching
    | (?:\bmerg[ei]\w*|\bdedup\b|\bduplicate\b)
    # Rebalance / tax / harvest / simulation
    | (?:\brebalanc\w*|\btax\b|\bharvest\b|\bsimulat\w*|\bforecast\w*|\bmontecarlo\b|\bmonte\s+carlo\b)
    # Explicit planning language
    | (?:\bplan\s+my\b|\bplanning\b|\bbudget\s+setup\b|\bbudget\s+plan\b)
    # Audit / analysis / deep-dive
    | (?:\baudit\b|\banaly[sz]\w*|\banalys[ie]\w*|\bcompare\b|\breview\s+my\b|\bdeep\s+dive\b|\bthorough\b)
    # Causal / migration / bulk
    | (?:\bmigrat\w*|\bbulk\b)
    # Code / scripting / agents / skills / scheduling
    | (?:\bscript\b|\bcode\b|\brun\s+code\b|\bskill\b|\bagent\b|\bschedule[d]?\s+(?:task|agent)\b)
    # Visual artifacts — chart/dashboard generation writes a whole HTML document;
    # the weak tier produces visibly broken layouts (squished donuts, nested
    # scrollbars), so visualization intent always gets the strong model.
    | (?:\bchart\w*|\bgraph\w*|\bdashboard\b|\bvisuali[sz]\w*|\bartifact\b|\bplot\b
        |\bdiagram\b|\binfographic\b|\bdonut\b|\bheatmap\b|\bsparkline\b)
    # Advice / judgment — investment decisions deserve the strong model even when short
    | (?:\bshould\s+i\b|\bwhat\s+do\s+you\s+think\b|\brecommend\w*|\badvi[cs]e\b|\bopinion\b
        |\bworth\s+(?:it|buying|selling|holding|keeping)\b|\bsell\s+or\s+(?:hold|keep)\b
        |\bhold\s+or\s+sell\b|\bcut\s+(?:my\s+)?loss\w*\b|\btake\s+profit\w*\b)
    """,
    re.IGNORECASE | re.VERBOSE,
)


def route(
    messages_text: str,
    has_attachments: bool,
    prior_route: "str | None",
) -> dict:
    """Classify one turn of conversation.

    Args:
        messages_text: The raw text of the new user message(s) for this turn.
        has_attachments: True when the turn carries file/image uploads.
        prior_route: The tier persisted from the previous turn ("weak" | "strong" | None).

    Returns:
        {"model_tier": "weak" | "strong", "reason": str}
    """
    # Rule 1: sticky escalation — never de-escalate mid-conversation.
    if prior_route == "strong":
        return {"model_tier": "strong", "reason": "sticky_escalation"}

    # Rule 2: attachments require parsing / vision — always strong.
    if has_attachments:
        return {"model_tier": "strong", "reason": "has_attachments"}

    text = (messages_text or "").strip()

    # Empty / ambiguous input → strong (quality-safe).
    if not text:
        return {"model_tier": "strong", "reason": "empty_input"}

    # Rule 3: long messages imply complexity.
    if len(text) > 350:
        return {"model_tier": "strong", "reason": "long_message"}

    # Rule 4: strong-intent keyword match.
    m = _STRONG_RE.search(text)
    if m:
        return {"model_tier": "strong", "reason": f"keyword:{m.group(0).strip().lower()}"}

    # Rule 5: everything else is weak.
    return {"model_tier": "weak", "reason": "simple_query"}
