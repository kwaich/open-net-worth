"""Contract tests for behaviour-based write classification.

The companion to ``tests/test_claim_contracts.py`` (which polices claims made in PROSE).
Skill ``allowed_tools`` is inert compatibility metadata, not an enforced allowlist (skills
are instructions, not tool policy — see skill_creator_bundle.py), so this file no longer
tests it. It still pins the classification that decides whether a tool is a read or a
write: 8 genuinely-writing tools were once classified as reads because the enforcement
sweep matched on NAME PREFIX rather than behaviour.
"""

from __future__ import annotations


# ── Write classification must stay behaviour-based ────────────────────────────
#
# The original sweep in tests/test_tool_domains.py matched on NAME PREFIX, which let 8
# genuine writers ship untagged because they were called get_*/list_*/propose_*. That file
# has since been inverted to a name-blind, review-based classification. These tests pin the
# property that inversion bought, so a future "simplification" back to a prefix regex fails
# here rather than quietly reopening the hole.
def test_write_classification_is_name_blind():
    """A read-NAMED tool that writes is classified as a writer — the exact original bug.

    get_watchlist/get_live_prices/list_match_reviews were here too until 2026-08-23, when
    each was changed to pass record_history=False/persist_sweep=False and genuinely stopped
    writing — see their own tool docstrings and
    test_confirm_gate_policy.py::test_the_exempt_class_is_the_expected_curated_set_not_a_heuristic.
    They are no longer valid examples of this bug shape (a tool that writes despite its
    name) because they no longer write at all."""
    from backend.ai.tools.registry import mutated_domains
    from tests.test_tool_domains import READ_ONLY_TOOLS

    for name in (
        "diff_statement",
        "verify_statement_balance",
        "scan_intel",
        "resolve_instruments",
    ):
        assert mutated_domains(name), (
            f"'{name}' writes but is no longer tagged — the classification has regressed "
            f"to trusting the name prefix."
        )
        assert name not in READ_ONLY_TOOLS, f"'{name}' writes; it cannot be READ_ONLY"


def test_a_new_write_tool_named_get_still_fails_the_sweep():
    """The sweep must catch an UNCLASSIFIED tool no matter what it is named.

    Registers a throwaway ``get_``-prefixed tool and asserts the classification sweep
    flags it, then removes it. Without this, "the sweep is name-blind" is an assertion
    about the code rather than a demonstrated property.
    """
    from backend.ai.tools import registry as reg
    from tests.test_tool_domains import (
        NO_DOMAIN_WRITE_TOOLS,
        READ_ONLY_TOOLS,
        _production_tool_names,
    )

    name = "get_totally_innocent_looking_thing"
    assert name not in reg._REGISTRY, "fixture name collides with a real tool"

    @reg.tool(name=name, description="pretends to be a read", input_schema={"type": "object"})
    async def _handler(_input_data):  # pragma: no cover - never dispatched
        return "{}", None

    try:
        # The same predicate test_every_tool_is_explicitly_classified applies, run over a
        # production-scoped name list with the fixture spliced in (the real sweep scopes to
        # backend.ai.tools modules, and this handler is defined in a test module).
        names = set(_production_tool_names()) | {name}
        tagged = reg.mutating_tool_domains()
        unclassified = sorted(
            n
            for n in names
            if n not in tagged and n not in READ_ONLY_TOOLS and n not in NO_DOMAIN_WRITE_TOOLS
        )
        assert name in unclassified, (
            "a brand-new tool named get_* slipped through the classification sweep — the "
            "sweep is matching on the name again."
        )
    finally:
        # Registry is a process-global; leaking the fixture would make every later
        # classification test order-dependent.
        reg._REGISTRY.pop(name, None)
        reg._validator_for.cache_clear()


def test_domains_the_frontend_refetches_on_are_all_produced_by_a_write_tool():
    """Every domain the frontend's core refetch listens for is emitted by some write tool.

    The reverse direction — a backend domain with no frontend subscriber — is NOT tested:
    measured against the tree, 16 domains are legitimately declared with no subscriber yet
    (``artifacts``, ``sandbox``, ``news_sources``, ``rules``, ``watchlist``, …), and
    failing on those would punish a backend that shipped ahead of its UI. That direction
    is REPORTED instead, by ``backend/scripts/audit_claims.py --domains``.

    This direction IS a true contract: a view waiting on a domain no tool emits refetches
    forever on nothing, and goes silently stale after every AI write.
    """
    from backend.ai.tools.registry import mutating_tool_domains

    # frontend/src/RouterShell.tsx's coreDataV subscription.
    subscribed = {"portfolio", "ledger", "accounts", "payees", "categories", "budget"}
    produced = {d for domains in mutating_tool_domains().values() for d in domains}
    orphans = sorted(subscribed - produced - {"*"})
    assert not orphans, (
        f"frontend/src/RouterShell.tsx subscribes to {orphans} via useDataVersion(), but no "
        f"write tool declares mutates=(...) for it — that view will never refresh after an "
        f"AI write. Either tag the tool that writes it, or drop the subscription."
    )
