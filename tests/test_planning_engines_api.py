"""API smoke tests for the pluggable-budget-engines REST surface (RFC
2026-08-22-pluggable-budget-engines.md, Phase 1) — GET /api/planning/engines, GET
/api/planning/view, GET /api/planning/engines/proportional/draft.

The old PUT /api/planning/engines/proportional/config route was removed with the
2026-09-26 GROUP-based redesign — 50/30/20's groups are the book's own Zero-Based groups
now, configured through POST /api/planning/verb like every other operation (see
tests/test_planning_proportional_api.py for that route's own contract tests).

Mirrors tests/test_planning_api.py's isolation strategy: a minimal FastAPI app mounting
ONLY the planning router, DB-backed stores redirected via db_isolated, and a fixed FX
cache/base currency. services/budget_engines/service.py holds its OWN local
load_fx_cache/base_currency bindings (distinct from services.planning's), so both must be
monkeypatched for a folded ViewModel's numbers to be deterministic.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.planning import router as planning_router
from backend.db.category_store import CategoryStore
from backend.db.profile_store import ProfileStore
from backend.models.category_defs import BUILTIN_CATEGORIES
from backend.services import envelopes as envelopes_service
from backend.services import ledger as svc
from backend.services import planning
from backend.services.budget_engines import service as budget_engines_service

_FOOD = "cat_13095ba70000"  # Food & Dining (built-in, stable id)


def _env(category_id: str) -> str:
    return envelopes_service.env_of()[category_id]


def _seed_zero_based_history():
    """One budget_assignments row — marks the book as an EXISTING zero-based budgeter, so
    `home_engine_id()` resolves to "zero_based". Without it a fresh test book now resolves to
    "proportional" (owner, 2026-08-23: a book that never budgeted starts on 50/30/20), which
    is correct behaviour but not what these tests are asserting about."""
    from backend.db.budget_assignments_store import BudgetAssignmentsStore

    BudgetAssignmentsStore().set_assigned("2026-08", "cat_13095ba70000", 100.0)


@pytest.fixture
def client(tmp_path, monkeypatch, db_isolated):
    fx = {"SGD": 1.0}
    monkeypatch.setattr(planning, "load_fx_cache", lambda: fx)
    monkeypatch.setattr(svc, "base_currency", lambda: "SGD")
    monkeypatch.setattr(budget_engines_service, "load_fx_cache", lambda: fx)
    monkeypatch.setattr(budget_engines_service, "base_currency", lambda: "SGD")

    ProfileStore().set_raw({"base_currency": "SGD"})
    # Real category rows — draft()/verbs need to resolve a real category id/label
    # (unlike ensure_envelopes_migrated's own "test env, pre-seed" static-list fallback).
    CategoryStore().bulk_insert(BUILTIN_CATEGORIES)
    envelopes_service.ensure_envelopes_migrated()
    # An on-budget account: since the 2026-09-25 eligibility amendment, BOTH engines'
    # envelope activity requires a leg's account to be a known plan-source account
    # (planning.plan_leg_ok) — a transaction with no account at all now folds nowhere,
    # matching Zero-Based's own long-standing rule.
    from backend.db.account_store import AccountStore

    AccountStore().create({"id": "bank", "name": "Bank", "type": "cash", "currency": "SGD"})
    app = FastAPI()
    app.include_router(planning_router)
    return TestClient(app)


# ── GET /api/planning/engines ────────────────────────────────────────────────────────


def test_list_engines_shape(client):
    _seed_zero_based_history()
    r = client.get("/api/planning/engines")
    assert r.status_code == 200
    body = r.json()
    assert body["home_engine_id"] == "zero_based"
    by_id = {e["id"]: e for e in body["engines"]}
    # Cash Envelopes (id "envelope") was removed on 2026-09-25.
    assert set(by_id) == {"zero_based", "proportional"}
    assert by_id["zero_based"]["configured"] is True
    assert by_id["zero_based"]["is_home"] is True
    assert by_id["proportional"]["configured"] is False
    assert by_id["proportional"]["kind"] == "derived"
    # Every engine's own declared verbs ride along on the wire (the generic setup pane
    # discovers a verb id/param name from this list rather than hardcoding one) — 50/30/20
    # declares 7 since the 2026-09-26 GROUP-based redesign: the method's groups are the
    # book's own Zero-Based groups, so the old set_targets/set_bucket/set_order/set_config
    # verbs are gone, replaced by set_group_targets/set_group_excluded.
    assign = next(v for v in by_id["zero_based"]["verbs"] if v["id"] == "assign")
    assert assign["label"] and assign["params"]
    assert {v["id"] for v in by_id["proportional"]["verbs"]} == {
        "configure",
        "set_group_targets",
        "set_group_excluded",
        "set_envelope_excluded",
        "set_cap",
        "set_caps",
        "copy_targets_to_caps",
        "set_income_categories",
    }
    # Display-name/description copy (owner, 2026-08-23 naming correction) — user-facing,
    # short, and carrying no model-facing marker (id/verb name/"NOTE FOR MODELS").
    assert by_id["zero_based"]["name"] == "Zero-Based"
    assert by_id["proportional"]["name"] == "50/30/20"
    for e in by_id.values():
        assert "NOTE FOR MODELS" not in e["description"]
        assert "model_note" not in e  # Lucius-only guidance never rides the REST wire


# ── Plan -> Budget introduction dismissal ────────────────────────────────────────────


def test_list_engines_reports_intro_dismissed_false_by_default(client):
    body = client.get("/api/planning/engines").json()
    assert body["intro_dismissed"] is False


def test_dismiss_budget_lens_intro_persists(client):
    r = client.post("/api/planning/budget-lens-intro/dismiss")
    assert r.status_code == 200
    assert r.json() == {"intro_dismissed": True}
    # And a fresh GET reflects it — persisted, not just echoed back.
    assert client.get("/api/planning/engines").json()["intro_dismissed"] is True


def test_dismiss_budget_lens_intro_is_idempotent(client):
    client.post("/api/planning/budget-lens-intro/dismiss")
    r = client.post("/api/planning/budget-lens-intro/dismiss")
    assert r.status_code == 200
    assert r.json() == {"intro_dismissed": True}


# ── GET /api/planning/view ───────────────────────────────────────────────────────────


def test_get_view_defaults_to_home_engine_and_current_month(client):
    _seed_zero_based_history()
    r = client.get("/api/planning/view")
    assert r.status_code == 200
    body = r.json()
    assert body["engine_id"] == "zero_based"
    assert len(body["period"]) == 7  # YYYY-MM


def test_get_view_zero_based_matches_budget_month(client):
    r = client.get("/api/planning/view", params={"engine": "zero_based", "ym": "2026-06"})
    assert r.status_code == 200
    body = r.json()
    table = client.get("/api/planning/month", params={"ym": "2026-06"}).json()
    assert body["headline"]["value"] == table["ready_to_assign"]
    assert body["rows_shape"] == "rows"


def test_get_view_for_the_removed_cash_envelope_engine_is_404(client):
    """Cash Envelopes (id "envelope") was removed on 2026-09-25."""
    r = client.get("/api/planning/view", params={"engine": "envelope", "ym": "2026-06"})
    assert r.status_code == 404
    assert "list_budget_engines" in r.json()["detail"]


def test_get_view_proportional_shape(client):
    r = client.get("/api/planning/view", params={"engine": "proportional", "ym": "2026-06"})
    assert r.status_code == 200
    body = r.json()
    assert body["engine_id"] == "proportional"
    assert body["period"] == "2026-06"
    assert body["base_currency"] == "SGD"
    assert body["rows_shape"] == "proportion-bars"
    # Groups are the book's own Zero-Based groups now (never a fixed "needs"/"wants" pair)
    # — the one guarantee that never varies is the final, synthetic Savings group.
    assert body["groups"][-1]["id"] == "savings"
    assert body["headline"]["unit"] == "percent"
    assert body["health"]["verdict"] in ("good", "attention", "bad", "unknown")


def test_get_view_no_caller_chosen_projection_param(client):
    """Hard invariant: no fields/include/projection param on this read."""
    r = client.get(
        "/api/planning/view",
        params={"engine": "proportional", "ym": "2026-06", "fields": "headline"},
    )
    assert r.status_code == 200
    body = r.json()
    # The extra query param is silently ignored (FastAPI drops unknown params) — the
    # ViewModel is still the FULL, server-shaped payload, not a projection of it.
    assert "groups" in body and "health" in body and "unmapped" in body


def test_get_view_unknown_engine_returns_404_naming_real_engines(client):
    r = client.get("/api/planning/view", params={"engine": "fifty_thirty_twenty"})
    assert r.status_code == 404
    assert "fifty_thirty_twenty" in r.json()["detail"]
    assert "list_budget_engines" in r.json()["detail"]


# ── GET /api/planning/engines/proportional/draft ────────────────────────────────────


def test_draft_proportional_shape(client):
    # draft() proposes a share of INCOME now — unlike the old category-keyword heuristic,
    # it needs an income figure on file, or there is honestly nothing to propose.
    ProfileStore().set_raw({"base_currency": "SGD", "monthly_net_income": 4000.0})
    r = client.get("/api/planning/engines/proportional/draft", params={"ym": "2026-06"})
    assert r.status_code == 200
    body = r.json()
    assert body["engine_id"] == "proportional"
    assert body["has_proposal"] is True
    proposal = body["proposed_state"]
    assert isinstance(proposal["group_targets"], dict)
    assert isinstance(proposal["rows"], list) and proposal["rows"]
    assert all({"group_id", "name", "avg_spend", "percent"} <= set(row) for row in proposal["rows"])


def test_draft_proportional_applies_nothing(client):
    before = client.get("/api/planning/engines").json()
    client.get("/api/planning/engines/proportional/draft")
    after = client.get("/api/planning/engines").json()
    assert before == after


def test_draft_proportional_defaults_ym_to_current_month(client):
    r = client.get("/api/planning/engines/proportional/draft")
    assert r.status_code == 200


# ── GET /api/planning/engines/{engine_id}/draft (generic) ───────────────────────────


def test_draft_engine_generic_zero_based_proposes_targets(client):
    """Zero-based has a real proposal now — a monthly target per envelope (owner,
    2026-08-23). It used to report has_proposal: False, which is why a new book got no
    assisted start on the method most people arrive looking for."""
    r = client.get("/api/planning/engines/zero_based/draft")
    assert r.status_code == 200
    body = r.json()
    assert body["engine_id"] == "zero_based"
    assert body["has_proposal"] is True
    rows = body["proposed_state"]["rows"]
    assert rows and {
        "envelope_id",
        "label",
        "low_confidence",
        "suggested_amount",
        "current_amount",
        "category_ids",
    } <= set(rows[0])


def test_draft_engine_generic_unknown_engine_returns_404_naming_real_engines(client):
    r = client.get("/api/planning/engines/fifty_thirty_twenty/draft")
    assert r.status_code == 404
    assert "fifty_thirty_twenty" in r.json()["detail"]
    assert "list_budget_engines" in r.json()["detail"]


def test_draft_engine_generic_applies_nothing(client):
    before = client.get("/api/planning/engines").json()
    client.get("/api/planning/engines/zero_based/draft")
    after = client.get("/api/planning/engines").json()
    assert before == after


def test_draft_engine_generic_defaults_ym_to_current_month(client):
    r = client.get("/api/planning/engines/zero_based/draft")
    assert r.status_code == 200


# ── POST /api/planning/home-engine (Phase 2) ────────────────────────────────────────


def test_post_home_engine_switches_and_returns_fresh_engines_list(client):
    _seed_zero_based_history()
    # 50/30/20 must be configured before it can be home (set_home_engine's guard) — an
    # empty configure call is enough (group_targets is optional).
    client.post(
        "/api/planning/verb",
        json={"engine_id": "proportional", "verb_id": "configure", "params": {}},
    )
    r = client.post("/api/planning/home-engine", json={"engine_id": "proportional"})
    assert r.status_code == 200
    body = r.json()
    assert body["home_engine_id"] == "proportional"
    by_id = {e["id"]: e for e in body["engines"]}
    assert by_id["proportional"]["is_home"] is True
    assert by_id["zero_based"]["is_home"] is False

    # And it really persisted — a fresh GET reflects the same switch.
    assert client.get("/api/planning/engines").json()["home_engine_id"] == "proportional"


def test_post_home_engine_refuses_the_removed_cash_envelope_engine(client):
    _seed_zero_based_history()
    r = client.post("/api/planning/home-engine", json={"engine_id": "envelope"})
    assert r.status_code == 404
    assert client.get("/api/planning/engines").json()["home_engine_id"] == "zero_based"


def test_post_home_engine_unknown_id_returns_404_naming_real_engines(client):
    _seed_zero_based_history()
    r = client.post("/api/planning/home-engine", json={"engine_id": "fifty_thirty_twenty"})
    assert r.status_code == 404
    assert "fifty_thirty_twenty" in r.json()["detail"]
    assert "list_budget_engines" in r.json()["detail"]
    # A rejected switch must leave the home lens untouched.
    assert client.get("/api/planning/engines").json()["home_engine_id"] == "zero_based"


# ── POST /api/planning/verb (Phase 3) ───────────────────────────────────────────────


def test_post_verb_zero_based_assign_lands_through_apply_budget_change(client):
    r = client.post(
        "/api/planning/verb",
        json={
            "engine_id": "zero_based",
            "verb_id": "assign",
            "params": {"month": "2026-06", "envelope_id": _env(_FOOD), "amount": 500},
        },
    )
    assert r.status_code == 200
    assert r.json() == {
        "ok": True,
        "month": "2026-06",
        "envelope_id": _env(_FOOD),
        "assigned": 500.0,
    }
    table = client.get("/api/planning/month", params={"ym": "2026-06"}).json()
    assert table["ready_to_assign"] is not None
    row = next(
        c for g in table["groups"] for c in g["categories"] if c["envelope_id"] == _env(_FOOD)
    )
    assert row["assigned"] == 500.0


def test_post_verb_unknown_engine_returns_404_naming_real_engines(client):
    r = client.post(
        "/api/planning/verb",
        json={"engine_id": "fifty_thirty_twenty", "verb_id": "assign", "params": {}},
    )
    assert r.status_code == 404
    assert "fifty_thirty_twenty" in r.json()["detail"]
    assert "list_budget_engines" in r.json()["detail"]


def test_post_verb_unknown_verb_returns_404_naming_real_verbs(client):
    r = client.post(
        "/api/planning/verb",
        json={"engine_id": "zero_based", "verb_id": "asign", "params": {}},
    )
    assert r.status_code == 404
    assert "asign" in r.json()["detail"]
    assert "'assign'" in r.json()["detail"]


def test_post_verb_on_the_removed_cash_envelope_engine_is_404(client):
    r = client.post(
        "/api/planning/verb",
        json={
            "engine_id": "envelope",
            "verb_id": "set_pots",
            "params": {"pots": [{"envelope_id": _env(_FOOD), "amount": 320}]},
        },
    )
    assert r.status_code == 404
    assert "list_budget_engines" in r.json()["detail"]


def test_post_verb_zero_based_is_checkpoint_covered_and_rewind_reverts(client):
    """Decision F/RFC: routing through apply_budget_change means a verb call inherits
    the checkpoint hook for free — proven directly, not assumed."""
    from backend.ai import checkpoint as cp

    ctx, token = cp.begin_turn("s-verb", 0, "assign via apply_budget_verb")
    try:
        r = client.post(
            "/api/planning/verb",
            json={
                "engine_id": "zero_based",
                "verb_id": "assign",
                "params": {
                    "month": "2026-06",
                    "envelope_id": _env(_FOOD),
                    "amount": 500,
                },
            },
        )
        assert r.status_code == 200
    finally:
        entry = cp.end_turn(ctx, token)

    assert entry is not None
    assert "budget_assignments" in entry["db_tables"]

    cp.restore(entry["id"], mode="data")
    table = client.get("/api/planning/month", params={"ym": "2026-06"}).json()
    row = next(
        c for g in table["groups"] for c in g["categories"] if c["envelope_id"] == _env(_FOOD)
    )
    assert row["assigned"] == 0.0
