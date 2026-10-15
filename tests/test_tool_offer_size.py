"""Cost-regression guard for the CORE tool offer size (Lane D).

These tests pin the interactive-default offer size so a gradual creep of tools
back into CORE_TOOL_NAMES cannot silently re-inflate the cold-start token cost.

Key invariants:
- Fresh interactive offer (no find_tools loaded) has ≤ 24 tools.
- find_tools is in the fresh offer.
- Representative rare/searchable tools are NOT in the fresh offer.
- The build_tool_catalog() char count stays inside a tight ratchet.
- The core offer JSON itself stays below 50 000 chars (~12 500 tokens).

All tests are offline + isolated. ContextVars are reset between tests.
"""

from __future__ import annotations

import json

import pytest

from backend.ai import assistant
from backend.ai.tools import TOOL_SCHEMAS, _skill_scope
from backend.ai.tools._extra_groups_scope import set_extra_groups, set_extra_names
from backend.ai.tools._groups import CORE_TOOL_NAMES


@pytest.fixture(autouse=True)
def _reset_scopes():
    """Reset all per-turn ContextVar state before/after each test."""
    _skill_scope.clear_scope()
    set_extra_groups(frozenset())
    set_extra_names(frozenset())
    yield
    _skill_scope.clear_scope()
    set_extra_groups(frozenset())
    set_extra_names(frozenset())


# ---------------------------------------------------------------------------
# 1. Fresh offer size: ≤ 24 tools
# ---------------------------------------------------------------------------


def test_fresh_interactive_offer_at_most_18_tools():
    """A fresh interactive session (no find_tools loaded) must have ≤ 18 tools.

    This pins the cold-start cost: every tool in the default offer contributes
    to the tools= payload sent to the provider every turn. The 2026-07-19
    evidence-based CORE diet (docs/adr/2026-07-19-core-tool-offer-diet.md) targets
    ~13 tools, measured against real per-tool session-spread over ~293 real Lucius
    sessions; this cap gives 5 slots of headroom for deliberate, evidence-backed growth.
    """
    offered = assistant._active_tools()
    count = len(offered)
    assert count <= 18, (
        f"Fresh interactive offer has {count} tools — over the 18-tool cap. "
        "Leave the new tool out of CORE_TOOL_NAMES — find_tools can load it on demand."
    )


def test_fresh_interactive_offer_at_least_10_tools():
    """The fresh offer must have ≥ 10 tools — a floor guards against accidental deletion."""
    offered = assistant._active_tools()
    count = len(offered)
    assert count >= 10, (
        f"Fresh interactive offer has only {count} tools — below the 10-tool floor. "
        "Check that CORE_TOOL_NAMES was not accidentally reduced."
    )


# ---------------------------------------------------------------------------
# 2. find_tools must be in the fresh offer
# ---------------------------------------------------------------------------


def test_find_tools_in_fresh_offer():
    """find_tools must always be in the fresh interactive offer.

    It is the only mechanism by which the model can search for and load additional
    tools.  Removing it from the offer would trap the model with only the core
    tools for the entire session.
    """
    offered_names = {t["name"] for t in assistant._active_tools()}
    assert "find_tools" in offered_names, (
        "find_tools is absent from the fresh interactive offer — "
        "the model cannot discover the rest of the registry without it."
    )


def test_find_tools_in_core_tool_names():
    """find_tools must be in CORE_TOOL_NAMES (set-level invariant)."""
    assert "find_tools" in CORE_TOOL_NAMES


# ---------------------------------------------------------------------------
# 3. Representative rare/searchable tools are NOT in the fresh offer
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "tool_name",
    [
        # Admin / deep-analysis tools — all search-loaded via find_tools(query=...)
        "simulate_retirement",
        "create_rule",
        "assign_budget",
        "create_agent",
        "email_send_message",
        "run_code",
        "get_equity_report",
        "list_rules",
        "get_budget_month",
        "list_skills",
        "web_search",
        "remember_fact",
        # Demoted 2026-07-19 (docs/adr/2026-07-19-core-tool-offer-diet.md) — measured
        # session-spread under 4%, no longer a permanent CORE slot.
        "get_ledger_summary",
        "get_spending_summary",
        "create_payees",
        "email_search_messages",
        "email_get_messages",
        "notify",
        "list_checkpoints",
        "restore_checkpoint",
    ],
)
def test_tool_not_in_fresh_offer(tool_name):
    """These tools must NOT appear in the fresh interactive offer.

    Everything outside CORE_TOOL_NAMES is search-loaded via find_tools(query=...).
    Their presence in the default offer would inflate the cold-start token cost.
    """
    offered_names = {t["name"] for t in assistant._active_tools()}
    assert tool_name not in offered_names, (
        f"'{tool_name}' is in the fresh interactive offer but should be search-loaded."
    )


# ---------------------------------------------------------------------------
# 4. Core offer JSON size bound (token cost guard)
# ---------------------------------------------------------------------------


def test_core_offer_json_size_under_55k_chars():
    """The core offer JSON must be under 55 000 chars (~13 750 tokens).

    The default offer is sent to the LLM on every interactive turn as the
    tools= payload.  At 55K chars this is ~13 750 tokens at 4 chars/token.
    Raised from 50K by the owner (2026-10-14) to keep recall_memory core.
    Breaching this cap means the CORE set is too large and some tools need to
    be demoted to find_tools groups.
    """
    offered = assistant._active_tools()
    size = len(json.dumps(offered))
    assert size < 55_000, (
        f"Core offer JSON is {size:,} chars — over the 55K cap. "
        f"Current core size: {len(offered)} tools. "
        "Demote verbose tools out of CORE_TOOL_NAMES."
    )


# ---------------------------------------------------------------------------
# 5. Catalog token estimate (system-prompt cost guard)
# ---------------------------------------------------------------------------


def test_catalog_size_ratchet():
    """build_tool_catalog() must stay inside a TIGHT band around its measured size.

    The catalog is embedded in the static system-prompt block on every turn, so its
    cost is paid continuously. The old cap (8 000 tokens vs. a ~1 384-token actual)
    was so loose it could never fire — a summary creeping back per tool would have
    fit under it 5× over. This band is a real ratchet:

    * UPPER — measured 5 537 chars after flattening; 7 000 leaves room for ~40 new
      tools (~30 chars each) before it trips. Re-running it deliberately means the
      registry genuinely grew; re-baseline in the same commit and say so.
    * LOWER — 3 000 chars guards the completeness contract from the other side: a
      silent truncation or an over-eager filter would otherwise pass unnoticed.

    A structural regression (per-tool summaries, or domain headers returning) blows
    through the upper bound immediately: summaries alone cost ~26 K chars.
    """
    from backend.ai.tools.catalog import build_tool_catalog

    char_count = len(build_tool_catalog())
    assert 3_000 < char_count < 7_000, (
        f"build_tool_catalog() is {char_count:,} chars — outside the 3 000–7 000 "
        "ratchet. If the registry legitimately grew, re-baseline this bound in the "
        "same commit; if a description or a domain header crept back in, remove it."
    )


def test_catalog_cost_per_tool_stays_names_only():
    """Amortized chars-per-tool pins the names-only contract independent of registry size.

    A bare name averages ~20 chars + 2 for the separator. 40 is a generous ceiling that
    still catches the smallest per-tool summary; the old absolute cap could not.
    """
    from backend.ai.tools.catalog import build_tool_catalog

    catalog = build_tool_catalog()
    per_tool = len(catalog) / len(TOOL_SCHEMAS)
    assert per_tool < 40, (
        f"catalog costs {per_tool:.1f} chars per tool — over the 40-char names-only "
        "ceiling. A per-tool description or label has crept back in."
    )


# ---------------------------------------------------------------------------
# 6. Full-registry sanity cap
# ---------------------------------------------------------------------------


def test_full_registry_has_manageable_size():
    """TOOL_SCHEMAS total should stay under a sanity cap (500 tools).

    Not a hard cost constraint — just a structural sanity check.  If the
    registry has grown past 500 entries, something unusual happened.
    """
    assert len(TOOL_SCHEMAS) < 500, (
        f"TOOL_SCHEMAS has {len(TOOL_SCHEMAS)} entries — check for duplicate registrations."
    )


def test_core_offer_subset_of_full_registry():
    """All tools in the core offer must be in TOOL_SCHEMAS."""
    all_names = {t["name"] for t in TOOL_SCHEMAS}
    core_offered = {t["name"] for t in assistant._active_tools()}
    extra = core_offered - all_names
    assert extra == set(), f"Core offer contains names absent from TOOL_SCHEMAS: {sorted(extra)}"
