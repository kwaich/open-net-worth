"""Regression (2026-08-12 live-log attribution): /api/planning/summary ran 727 SQL
statements per request — 661 of them from ``planning._assigned_amt``, because
``_available``'s carry walk read assigned at every (month × envelope) node and each read
was its own ``month_map()`` SELECT re-fetching one of ~20 distinct month maps. p50 2.1s,
every request, all day.

``_build_context`` now loads the WHOLE assignments table once (``all_map()`` →
``ctx["_assigned_by_month"]``) and the read paths go through ``_ctx_assigned`` — the same
one-load-threaded-through-the-request shape as ``ctx["txns"]``/``ctx["rates"]`` (and the
cashflow ``all_txns`` seam). Write paths keep calling ``_assigned_amt`` for a fresh read
at the choke.

Pins the store-call counts BOUNDED BY A SMALL CONSTANT — never scaling with
months × envelopes (measured post-fix: 725 → 66 statements on real data).
"""

from __future__ import annotations

import pytest

from backend.db.budget_assignments_store import BudgetAssignmentsStore
from backend.db.profile_store import ProfileStore
from backend.services import envelopes as envelopes_service
from backend.services import ledger as svc
from backend.services import planning


@pytest.fixture
def env(monkeypatch, db_isolated):
    monkeypatch.setattr(planning, "load_fx_cache", lambda: {"SGD": 1.0})
    monkeypatch.setattr(svc, "base_currency", lambda: "SGD")
    ProfileStore().set_raw({"base_currency": "SGD"})
    svc.accounts.replace_all(
        [
            {
                "id": "bank",
                "name": "Bank",
                "kind": "asset",
                "type": "cash",
                "currency": "SGD",
                "initial_balance": 0.0,
            }
        ]
    )
    svc.transactions.replace_all([])
    envelopes_service.ensure_envelopes_migrated()


def _count_calls(monkeypatch, cls, method_name):
    calls: list = []
    original = getattr(cls, method_name)

    def counting(self, *args, **kwargs):
        calls.append((args, kwargs))
        return original(self, *args, **kwargs)

    monkeypatch.setattr(cls, method_name, counting)
    return calls


def test_budget_aggregates_assignment_reads_do_not_scale_with_months_x_envelopes(env, monkeypatch):
    # Deep history: assignments in 12 distinct months across every migrated envelope —
    # the pre-fix shape read months × envelopes maps (hundreds of SELECTs); the fix
    # reads the table once per context.
    envelope_ids = [e["id"] for e in envelopes_service.all_envelopes()][:8]
    assert len(envelope_ids) >= 3, "builtin envelope migration should mint several"
    store = planning.assignments_store
    for m in range(1, 13):
        month = f"2025-{m:02d}"
        for env_id in envelope_ids:
            store.set_assigned(month, env_id, 10.0)
    store.set_assigned("2026-01", envelope_ids[0], 25.0)

    month_map_calls = _count_calls(monkeypatch, BudgetAssignmentsStore, "month_map")
    all_map_calls = _count_calls(monkeypatch, BudgetAssignmentsStore, "all_map")

    from backend.services import planning_summary

    out = planning_summary.budget_aggregates("2026-01")

    assert out["month"] == "2026-01"
    assert isinstance(out["envelopes"], dict)
    assert len(month_map_calls) <= 2, (
        f"month_map() called {len(month_map_calls)} times during budget_aggregates — "
        "the read path must use ctx['_assigned_by_month'] (one all_map load per context), "
        "not a per-(month × envelope) store read (the 661-of-727-statements regression)"
    )
    assert len(all_map_calls) <= 8, (
        f"all_map() called {len(all_map_calls)} times — one per _build_context (plus "
        "_assigned_to_date_map), bounded by the number of contexts, never by data size"
    )


def test_ctx_assigned_reads_the_same_numbers_the_store_holds(env):
    envelope_ids = [e["id"] for e in envelopes_service.all_envelopes()][:2]
    planning.assignments_store.set_assigned("2026-02", envelope_ids[0], 55.5)

    ctx = planning._build_context("2026-02")

    assert planning._ctx_assigned(ctx, "2026-02", envelope_ids[0]) == 55.5
    assert planning._ctx_assigned(ctx, "2026-02", envelope_ids[1]) == 0.0
    assert planning._ctx_assigned(ctx, "2031-01", envelope_ids[0]) == 0.0
    # …and matches the direct store read the write paths still use.
    assert planning._assigned_amt("2026-02", envelope_ids[0]) == 55.5
