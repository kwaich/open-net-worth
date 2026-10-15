"""Contract tests for the CLAIMS the model is told — layer ④ of the reliability program.

The audit of 2026-07-18 found, in a codebase with 10,000+ passing tests: 50 of 286 tool
descriptions carrying a stale claim, three tool names in the system prompt that do not
exist, a category list of which a THIRD does not resolve, a 156-tool skill invisible for
months because two names in its stored allowlist had been renamed, and CLAUDE.md committed
with merge-conflict markers. Every one of those is machine-checkable. Nothing was checking.

These tests check them. The governing constraint is PRECISION: a contract test that cries
wolf gets disabled, and then the rot comes back with a test file standing over it as
cover. Every heuristic below was measured against the current tree before it was accepted,
and the numbers are recorded where the heuristic is defined.

This module polices claims made in PROSE. Its companions:
  * ``tests/test_claim_allowlists.py``   — claims made in DATA (stored ``allowed_tools``)
    plus the behaviour-based read/write classification;
  * ``tests/claim_registry.py``          — the shared "what is a real tool name" judgement;
  * ``tests/claim_sources.py``           — the shared text extraction;
  * ``backend/scripts/audit_claims.py``  — the checks too fuzzy or too live-data-bound to
    gate a commit (duplicated rules, the operator's real playbooks, orphan domains);
  * ``backend/scripts/prose_staleness.py`` — workflow rot: prose whose service moved on.

── The tool-name heuristic, and why it is trustworthy ────────────────────────────────

Prose is full of snake_case that is not a tool name. The detector therefore anchors on
things that are true by construction rather than on a hand-written word list:

  1. A candidate must be snake_case whose FIRST SEGMENT is a verb some REAL registered
     tool uses (``get_``/``apply_``/``propose_``/…). The verb set is derived from the live
     registry, so it teaches itself new tool families.
  2. Tokens in the SCHEMA FIELD VOCABULARY — every property name, ``required`` entry and
     ``enum`` value across all tool schemas — are field references, not tool references.
     This is what removes ``agent_id``, ``merge_into``, ``run_now``, ``search_queries``.
  3. What remains is judged by SURFACE, in two tiers — see ``tier_a_dead_tokens``
     (instruction surfaces) and ``tier_b_dead_tokens`` (tool descriptions) in
     ``tests/claim_registry.py``.

Measured on the tree at the time of writing (272 tools, 272 descriptions, 726 parameter
descriptions, 266 prompt literals): Tier A yields exactly 3 findings on ``prompts.py``,
all three genuine (``get_cashflow``, ``update_position``, ``delete_transaction``) — 0
false positives. Tier B yields 4 findings across the 998 description texts, all 4 domain
vocabulary rather than dead references, recorded as reviewed exemptions in ``NOT_A_TOOL``
— a measured false-positive rate of 4/998. The variant WITHOUT the schema-vocabulary
subtraction produced 26 findings of which 23 were false, and the variant that also
excused live backend symbols on the PROMPT surface silently swallowed all 3 real defects;
both were rejected on those measurements.

── Marked-as-expected rot ────────────────────────────────────────────────────────────

Checks that fail on rot other lanes are still fixing are marked ``xfail(strict=True)``.
Strict is deliberate: when the fix lands the test XPASSes and goes RED, which forces the
marker to be deleted rather than left behind as a permanently silent test. A silently
passing xfail is precisely the "nothing was checking" failure this file exists to end.
Each marker names the defect and where it lives; that set is the outstanding-rot inventory.
"""

from __future__ import annotations

import re
from pathlib import Path

from tests.claim_registry import (
    NOT_A_TOOL,
    fmt,
    live_tools,
    nearest,
    schemas,
    tier_a_dead_tokens,
    tier_b_dead_tokens,
)
from tests.claim_sources import (
    PROMPTS_PY,
    SKILL_CREATOR_BUNDLE_PY,
    SKILL_STORE_PY,
    STATEMENT_BUNDLE_PY,
    Claim,
    files_with_conflict_markers,
    line_of,
    python_string_claims,
)

# ── 0. The exemption list must not become a mute button ───────────────────────


def test_not_a_tool_exemptions_are_not_stale():
    """An exemption must never shadow a REAL tool, and must carry a reason.

    The dangerous direction is a name later becoming a live tool: the exemption would then
    silently suppress every genuine claim about it. (An exemption that merely stops
    appearing in any prose is harmless and is NOT policed, so this never churns for the
    lanes rewriting descriptions.)
    """
    for name, reason in NOT_A_TOOL.items():
        assert reason.strip(), f"NOT_A_TOOL['{name}'] needs a reason"
        assert name not in live_tools(), (
            f"'{name}' is now a REGISTERED TOOL but is still listed in NOT_A_TOOL "
            f"(tests/claim_registry.py), which suppresses every claim check about it. "
            f"Remove the exemption."
        )


# ── 1. No claim may name a tool that does not exist ───────────────────────────


def _prompt_claims() -> list[Claim]:
    claims = python_string_claims(PROMPTS_PY, "prompts.py")
    assert len(claims) > 50, (
        f"only {len(claims)} string literals extracted from {PROMPTS_PY} — the extractor "
        "is checking almost nothing. Did the prompt move out of this module?"
    )
    return claims


def test_system_prompts_name_onlylive_tools():
    """Every tool-shaped name in the system prompts resolves to a callable tool.

    Covers both prompt variants — ``build_system_prompt`` (interactive) and
    ``build_lean_system_prompt`` (agent/headless) both live in this module, so scanning
    its literals covers every variant in one pass.
    """
    findings = []
    for claim in _prompt_claims():
        for token in sorted(tier_a_dead_tokens(claim.text)):
            line = line_of(claim, token)
            findings.append(
                f"{claim.path}:{line}: prompt tells the model to use '{token}', which is "
                f"not a registered tool. Closest live tool: {nearest(token, live_tools())}"
            )
    assert not findings, (
        "The system prompt names tools that do not exist. The model will emit them and "
        "get 'unknown tool' back:\n" + fmt(findings)
    )


def test_tool_descriptions_name_only_live_tools_or_live_symbols():
    """No tool description points the model at a dead tool or a removed service function.

    Tier B: a live backend symbol is tolerated (descriptions explain internals), so what
    this catches is a name that has been RENAMED or DELETED out from under the prose —
    the workflow-rot class where the tool itself is internally consistent.
    """
    findings = []
    for schema in schemas():
        texts = [("description", schema["description"])]
        props = (schema.get("input_schema") or {}).get("properties") or {}
        for prop_name, prop in props.items():
            if isinstance(prop, dict) and isinstance(prop.get("description"), str):
                texts.append((f"param '{prop_name}'", prop["description"]))
        for where, text in texts:
            for token in sorted(tier_b_dead_tokens(text)):
                findings.append(
                    f"tool '{schema['name']}' ({where}) references '{token}', which is "
                    f"neither a registered tool nor a live backend symbol. Closest live "
                    f"tool: {nearest(token, live_tools())}. If it is domain vocabulary, "
                    f"add it to NOT_A_TOOL in {Path(__file__).name} with a reason."
                )
    assert not findings, "Tool descriptions carry dead references:\n" + fmt(findings)


def test_bundled_skill_and_starter_prose_names_onlylive_tools():
    """Bundled skill bodies + starter-skill prose are instruction surfaces (Tier A)."""
    findings = []
    for path in (STATEMENT_BUNDLE_PY, SKILL_CREATOR_BUNDLE_PY, SKILL_STORE_PY):
        for claim in python_string_claims(path):
            for token in sorted(tier_a_dead_tokens(claim.text)):
                findings.append(
                    f"{claim.path}:{line_of(claim, token)}: skill prose instructs "
                    f"'{token}', not a registered tool. Closest: "
                    f"{nearest(token, live_tools())}"
                )
    assert not findings, "Bundled/starter skill prose names dead tools:\n" + fmt(findings)


# ── 2. Closed vocabularies must actually resolve ──────────────────────────────


def _prompt_lines() -> list[str]:
    return PROMPTS_PY.read_text(encoding="utf-8").splitlines()


def test_prompt_has_no_hardcoded_category_vocabulary():
    """OUTSTANDING ROT this test used to xfail on (owned by the prompts lane, fixed
    2026-07-19): the prompt's category list under a "CATEGORIES are SPENDING LABELS"
    marker advertised 12 slugs of which 4 did not resolve — 'food' (label is 'Food &
    Dining'), 'health' ('Health & Fitness'), 'family' ('Family & Gifts'), 'hobby'
    ('Hobby & Projects'). resolve_category_ref does an EXACT label match, so these
    silently booked as Uncategorized while their 8 siblings worked — an intermittent failure.

    The fix is architectural, not a 4-label correction: the harness-audit-2026-07-18
    system-prompt trim (.claude/rfcs/harness-audit-2026-07-18/) removed the hardcoded
    list entirely rather than fixing it, because ANY static category list in the prompt
    can only drift out of sync with the real category store again (categories are fully
    user-renameable/deletable) — list_categories is the single source of truth for what
    exists, and an unknown-category write already gets a prescriptive error naming the
    closest real labels (services/ledger.py's unknown-category message). This test now
    guards against the anti-pattern reappearing rather than checking a list that no
    longer exists.
    """
    text = PROMPTS_PY.read_text(encoding="utf-8")
    assert "CATEGORIES are SPENDING LABELS" not in text, (
        "a hardcoded category-slug list is back in the system prompt — categories are "
        "user-renameable/deletable, so any static list here will drift stale again. "
        "Prefer pointing the model at list_categories (plus the unknown-category "
        "error's own recovery hint) over enumerating labels in prose."
    )


def test_prompt_kind_vocabulary_is_valid():
    """Every kind enumerated in a prompt kind-block is a real transaction kind."""
    from backend.services.categories import _VALID_KINDS

    lines = _prompt_lines()
    listed: list[tuple[int, str]] = []
    for i, line in enumerate(lines, 1):
        match = re.match(r"^\s{2}([a-z_]+)\s+—", line)
        if not match:
            continue
        header = "\n".join(lines[max(0, i - 12) : i])
        if "TRANSACTION KIND" in header or "TRANSACTION TYPE" in header:
            listed.append((i, match.group(1)))
    assert len(listed) >= 4, (
        "found no enumerated transaction-kind block in the prompt — this check is "
        "silently verifying nothing. Re-anchor it on the current prompt layout."
    )

    findings = [
        f"backend/ai/prompts.py:{lineno}: prompt lists '{kind}' as a transaction kind; "
        f"valid kinds are {list(_VALID_KINDS)}."
        for lineno, kind in listed
        if kind not in _VALID_KINDS
    ]
    assert not findings, "The prompt teaches invalid transaction kinds:\n" + fmt(findings)


def test_tool_schema_kind_enums_match_the_service():
    """Any tool schema enumerating transaction kinds agrees with the service's own list."""
    from backend.services.categories import _VALID_KINDS

    valid = set(_VALID_KINDS)
    findings = []
    for schema in schemas():
        props = (schema.get("input_schema") or {}).get("properties") or {}
        prop = props.get("kind")
        if not isinstance(prop, dict) or not isinstance(prop.get("enum"), list):
            continue
        # Only transaction-kind enums: identified by overlapping with the real kinds.
        declared = {str(v) for v in prop["enum"]}
        if not declared & valid:
            continue
        extra = declared - valid
        if extra:
            findings.append(
                f"tool '{schema['name']}' declares kind enum values {sorted(extra)} that "
                f"the ledger rejects; valid kinds are {sorted(valid)}."
            )
    assert not findings, "Tool schemas declare invalid transaction kinds:\n" + fmt(findings)


# ── 4. A claim must not contradict itself ─────────────────────────────────────
#
# Tonight's audit found six rules stated in BOTH a tool description and the system prompt,
# and sibling tools appearing to disagree. "Assert the rule is stated once" turned out NOT
# to be reliably decidable: paraphrase detection over prose is fuzzy, and two tools can
# legitimately make opposite reversibility claims about genuinely different operations
# (merge_transactions IS reversible; resolve_match_review genuinely is not). Rather than
# ship a fuzzy test that would be disabled on its first false alarm, cross-source
# duplication is REPORTED by backend/scripts/audit_claims.py, and the deterministic core
# is tested here: a single description must not assert both polarities of one property.

_REVERSIBLE = re.compile(
    r"\b(?:is |stays |fully )?reversible\b|\bcan be undone\b|\bundoable\b", re.I
)
_IRREVERSIBLE = re.compile(
    r"\b(?:ir|not )reversible\b|\bcannot be undone\b|\bcan't be undone\b|\bpermanent(?:ly)? "
    r"(?:delete|remove)",
    re.I,
)


def test_no_description_claims_both_reversible_and_irreversible():
    """One description must not assert both polarities — that teaches the model nothing.

    Deterministic by construction: it compares a description against ITSELF, so there is
    no cross-tool paraphrase judgement to get wrong.
    """
    findings = []
    for schema in schemas():
        text = schema["description"]
        # An irreversibility phrase ("not reversible") also matches the positive pattern;
        # strip the negative matches before testing for a positive claim.
        positive_zone = _IRREVERSIBLE.sub(" ", text)
        if _IRREVERSIBLE.search(text) and _REVERSIBLE.search(positive_zone):
            findings.append(
                f"tool '{schema['name']}' claims BOTH reversible and irreversible in one "
                f"description. Pick one and state it once."
            )
    assert not findings, "Self-contradictory reversibility claims:\n" + fmt(findings)


# ── 5. No source file may contain merge-conflict markers ──────────────────────


def test_no_unresolved_merge_conflict_markers():
    """CLAUDE.md shipped with these. Trivial to check; caught a real committed defect."""
    hits = files_with_conflict_markers()
    findings = [f"{path}:{line}: {text}" for path, line, text in hits]
    assert not findings, (
        "Unresolved merge-conflict markers are committed. Everything after the marker is "
        "read by humans AND by the model as if it were true:\n" + fmt(findings)
    )
