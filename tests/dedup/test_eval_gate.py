"""Deterministic gate for the dedup accuracy harness (RFC transaction-dedup-engine).

Asserts the two hard-gate invariants that must hold for every CI run:
  1. False-merge rate == 0 on the must-NOT-merge / must-not-drop set.
  2. All idempotency / exact cases (must=noop) correctly return noop.

L3-pending cases (pending_l3=True in the fixture) are explicitly excluded from
the gate — the deterministic engine (L0–L2) is not expected to decide them.

phase=hardening fixtures are also excluded from the gate: they encode the
POST-hardening expected behavior. Other lanes are landing the engine fixes in
parallel; a later pass will flip them to standard. They are still evaluated for
the accuracy baseline snapshot but must not break CI.

Run as part of ``make check`` / ``pytest -m "not integration"``.
"""

from __future__ import annotations

import pytest

from backend.scripts.dedup_eval import _MUST_NOT_MERGE_VALUES, _evaluate_case, load_fixtures


@pytest.fixture(scope="module")
def all_results(tmp_path_factory):
    """Evaluate every fixture case once (module-scoped for speed).

    Hardening fixtures are loaded (so they appear in the accuracy snapshot) but
    are marked gate_excluded=True so the gate assertions below skip them.

    ``_evaluate_case`` swaps the ledger module's ``accounts``/``transactions``
    singletons to per-case tmp stores (CLI-script behavior: the process normally
    exits). Running in-process, we must restore them or every test after this
    module reads the LAST eval case's data instead of the shared test DATA_DIR.
    """
    from backend.services import ledger as ledger_service

    prior_accounts = ledger_service.accounts
    prior_transactions = ledger_service.transactions
    try:
        cases = load_fixtures(include_hardening=False)  # hardening excluded from gate
        results = []
        for i, case in enumerate(cases):
            case_tmp = tmp_path_factory.mktemp(f"case_{i:03d}_")
            results.append(_evaluate_case(case, case_tmp))
        yield results
    finally:
        ledger_service.accounts = prior_accounts
        ledger_service.transactions = prior_transactions


@pytest.fixture(scope="module")
def standard_results(all_results):
    """Non-hardening, non-L3-pending results — the deterministic gate set."""
    return [r for r in all_results if not r.gate_excluded and not r.pending_l3]


def test_false_merge_rate_is_zero(standard_results):
    """Hard gate: the engine must NEVER flag a must-NOT-merge case for review/merge.
    False merges silently destroy real transactions — the deterministic invariants
    must hold at 100% without LLM involvement."""
    false_merges = [r for r in standard_results if r.false_merge]
    ids = [r.case_id for r in false_merges]
    assert false_merges == [], (
        f"False merges on must-NOT cases: {ids}. "
        "Deterministic engine must not flag these for review."
    )


def test_idempotency_cases_all_noop(standard_results):
    """Hard gate: exact re-import / re-scan cases (must=noop) must be noop.
    Any other outcome breaks the idempotency contract at the write choke."""
    noop_failures = [r for r in standard_results if r.must == "noop" and r.engine_action != "noop"]
    ids = [r.case_id for r in noop_failures]
    assert noop_failures == [], (
        f"Idempotency cases returned non-noop: {ids}. "
        "Exact (external_source, external_ref) re-import must always be a no-op."
    )


def test_must_not_cases_never_review(standard_results):
    """Supplementary check: every must-NOT case that the deterministic engine handles
    must not return 'review'. Redundant with test_false_merge_rate_is_zero but explicit."""
    bad = [
        r
        for r in standard_results
        if r.must in _MUST_NOT_MERGE_VALUES and r.engine_action == "review"
    ]
    assert bad == [], [r.case_id for r in bad]


def test_l3_pending_cases_excluded_from_gate(all_results):
    """Sanity: L3-pending cases are flagged in fixtures and never counted against the
    deterministic gate (they are expected-pending-LLM, tracked but not failing)."""
    pending = [r for r in all_results if r.pending_l3]
    assert len(pending) >= 1, "Expected at least one L3-pending case in fixtures"
    pending_false_merges = [r for r in pending if r.false_merge]
    assert pending_false_merges == [], (
        "L3-pending cases must not be counted as false merges: "
        + str([r.case_id for r in pending_false_merges])
    )


def test_hardening_fixtures_are_gate_excluded(all_results):
    """Sanity: hardening fixtures are loaded but marked gate_excluded=True so they
    never break the deterministic gate during parallel engine-hardening work."""
    hardening = [r for r in all_results if r.phase == "hardening"]
    assert len(hardening) >= 1, "Expected at least one phase=hardening fixture"
    not_excluded = [r for r in hardening if not r.gate_excluded]
    assert not_excluded == [], "Hardening fixtures must be gate_excluded: " + str(
        [r.case_id for r in not_excluded]
    )


def test_all_deterministic_scenarios_pass(standard_results):
    """Full deterministic pass: every standard (non-L3-pending, non-hardening) case
    should match its expected engine outcome."""
    failures = [r for r in standard_results if not r.passed]
    summary = [
        f"{r.case_id}: expected engine_action={r.expected_action!r} "
        f"class={r.expected_class!r}, got action={r.engine_action!r} class={r.engine_class!r}"
        for r in failures
    ]
    assert failures == [], "Deterministic scenario failures:\n" + "\n".join(summary)
