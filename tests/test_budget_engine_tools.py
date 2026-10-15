"""AI tool-dispatch tests for the pluggable-budget-engines Lucius surface
(``ai/tools/budget_engines.py``, RFC ``.claude/rfcs/2026-08-22-pluggable-budget-engines.md``
Decision F).

Mirrors ``tests/test_planning_tools.py``'s isolation strategy (db_isolated + a fake FX
cache + the first-boot envelope migration) since ``get_budget_view``/
``draft_budget_engine_config`` fold through ``ZeroBasedEngine``, which itself delegates to
``services.planning`` (Decision D). Covers each read tool's happy path, the
default-to-home-engine/current-month behavior, an unknown engine_id naming the real
registered engines (``core.errors.recovery_hint``), and ``draft_budget_engine_config`` on
zero_based reporting "nothing to propose" rather than raising — plus, since Phase 2/3
(same RFC), the two write tools: ``apply_budget_operation`` (one happy path per engine, an
unknown engine/verb naming the real ones, zero_based landing through
``apply_budget_change``) and ``set_home_budget_engine`` (switches + persists, an unknown
id naming the real engines and leaving the home lens untouched).

50/30/20's own operations (``configure``/``set_group_targets``/``set_group_excluded``/
``set_cap``/``set_caps``/``copy_targets_to_caps``/``set_income_categories``) reflect the
2026-09-26 GROUP-based redesign — the method's groups are the book's own Zero-Based
groups, so there is no more per-envelope bucket verb to set.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from backend.ai.tools import TOOL_SCHEMAS, execute_tool
from backend.services import envelopes as envelopes_service
from backend.services import ledger as svc
from backend.services import planning


def _seed_zero_based_history():
    """One budget_assignments row — marks the book as an EXISTING zero-based budgeter, so
    `home_engine_id()` resolves to "zero_based". Without it a fresh test book now resolves to
    "proportional" (owner, 2026-08-23: a book that never budgeted starts on 50/30/20), which
    is correct behaviour but not what these tests are asserting about."""
    from backend.db.budget_assignments_store import BudgetAssignmentsStore

    BudgetAssignmentsStore().set_assigned("2026-08", "cat_13095ba70000", 100.0)


@pytest.fixture
def isolated(tmp_path, monkeypatch, db_isolated):
    """Same isolation as test_planning_tools.py's `isolated` fixture: DB-backed stores
    redirected to a fresh empty database (db_isolated), a fixed FX cache, SGD base
    currency, and the first-boot migration that mints a real 1:1 envelope per builtin
    expense category."""
    from backend.db.profile_store import ProfileStore

    monkeypatch.setattr(planning, "load_fx_cache", lambda: {"SGD": 1.0})
    monkeypatch.setattr(svc, "base_currency", lambda: "SGD")
    ProfileStore().set_raw({"base_currency": "SGD"})
    envelopes_service.ensure_envelopes_migrated()
    return tmp_path


def _name(env_id: str) -> str:
    return next(e["name"] for e in envelopes_service.all_envelopes() if e["id"] == env_id)


def call(tool, **inp):
    text, proposed = asyncio.run(execute_tool(tool, inp))
    return json.loads(text), proposed


def _env(key: str) -> str:
    return envelopes_service.env_of()[key]


def _create_group(name: str) -> str:
    """A real Zero-Based group — 50/30/20's groups are the book's own now, so a test that
    sets a group target/exclusion needs a real one to reference."""
    result = planning.apply_budget_change({"type": "group_create", "name": name})
    return (result.get("group") or result)["id"]


def test_tools_registered():
    names = {t["name"] for t in TOOL_SCHEMAS}
    assert {
        "list_budget_engines",
        "get_budget_view",
        "draft_budget_engine_config",
        "apply_budget_operation",
        "set_home_budget_engine",
    } <= names


# ── list_budget_engines ─────────────────────────────────────────────────────────────


def test_list_budget_engines_read(isolated):
    _seed_zero_based_history()
    body, proposed = call("list_budget_engines")
    assert proposed is None  # read → no proposed_change
    assert body["home_engine_id"] == "zero_based"
    assert len(body["engines"]) == 2
    by_id = {e["id"]: e for e in body["engines"]}
    assert set(by_id) == {"zero_based", "proportional"}  # Cash Envelopes removed 2026-09-25

    zero_based = by_id["zero_based"]
    assert zero_based["name"]
    assert zero_based["question"]
    assert zero_based["kind"] == "configured"
    assert zero_based["render"] == "custom"
    assert zero_based["configured"] is True
    assert zero_based["is_home"] is True

    proportional = by_id["proportional"]
    assert proportional["name"]
    assert proportional["question"]
    assert proportional["kind"] == "derived"
    assert proportional["render"] == "generic"
    assert proportional["is_home"] is False
    # A derived engine can still own real per-book state (its group targets) that must
    # actually be set before it's "configured" — never set here, so False.
    assert proportional["configured"] is False


def test_list_budget_engines_carries_model_note_for_lucius_only(isolated):
    """The disambiguation guidance (owner, 2026-08-23 naming correction; extended
    2026-09-25/-26 for 50/30/20's own group-based redesign) rides on the AI-TOOL surface
    only, as its own `model_note` field — never inside `description` (that field is
    strictly user-facing; GenericEngineSetup/ProportionalSetup render it verbatim, so a
    model-facing paragraph landing there was the incident this split fixes) and never on
    the REST wire (tests/test_planning_engines_api.py's own assertion covers that side)."""
    _seed_zero_based_history()
    body, _ = call("list_budget_engines")
    by_id = {e["id"]: e for e in body["engines"]}

    for e in by_id.values():
        assert "NOTE FOR MODELS" not in e["description"]

    assert "YNAB" in by_id["zero_based"]["model_note"]
    # 50/30/20's model_note explains the group-based onboarding order (configure first).
    assert "configure" in by_id["proportional"]["model_note"]


# ── get_budget_view ──────────────────────────────────────────────────────────────────


def test_get_budget_view_defaults_to_home_engine_and_current_month(isolated):
    _seed_zero_based_history()
    body, proposed = call("get_budget_view")
    assert proposed is None
    assert body["engine_id"] == "zero_based"
    assert len(body["period"]) == 7  # YYYY-MM, defaulted like get_budget_month


def test_get_budget_view_explicit_engine_and_period(isolated):
    body, _ = call("get_budget_view", engine_id="zero_based", period="2026-06")
    assert body["engine_id"] == "zero_based"
    assert body["period"] == "2026-06"
    assert body["base_currency"] == "SGD"
    assert body["rows_shape"] == "rows"
    assert body["headline"]["unit"] == "money"
    assert isinstance(body["groups"], list)
    assert body["health"]["verdict"] in ("good", "attention", "bad", "unknown")


def test_get_budget_view_for_the_removed_cash_envelope_engine_is_an_error(isolated):
    """Cash Envelopes (id "envelope") was removed on 2026-09-25 — asking for its view is an
    ordinary unknown-engine error naming the tool that lists the real ones."""
    body, _ = call("get_budget_view", engine_id="envelope", period="2026-06")
    assert "envelope" in body["error"]
    assert "list_budget_engines" in body["error"]


def test_get_budget_view_matches_budget_month_numbers(isolated):
    """The generic ViewModel surface must show the SAME numbers as the native
    get_budget_month tool — Decision D's equivalence guarantee, proven at the AI-tool
    layer (not just the service layer test_budget_engine_equivalence.py already covers)."""
    # Arrange through the write choke: ``apply_budget_change`` is deleted (the propose tool
    # writes on approval — ADR 2026-08-25-one-confirm-mechanism-for-every-tool).
    from backend.services import planning as _svc

    _svc.apply_budget_change(
        {
            "type": "assign",
            "month": "2026-06",
            "envelope_id": _env("cat_13095ba70000"),
            "amount": 800,
        }
    )
    view, _ = call("get_budget_view", period="2026-06")
    table, _ = call("get_budget_month", month="2026-06")

    assert view["headline"]["value"] == table["ready_to_assign"]

    food_row = next(
        row for g in view["groups"] for row in g["rows"] if row["id"] == _env("cat_13095ba70000")
    )
    food_measures = {m["key"]: m["amount_base"] for m in food_row["measures"]}
    assert food_measures["assigned"] == 800.0

    food_cell = next(
        c
        for g in table["groups"]
        for c in g["categories"]
        if "cat_13095ba70000" in c["category_ids"]
    )
    assert food_measures["assigned"] == food_cell["assigned"]
    assert food_measures["activity"] == food_cell["activity"]
    assert food_measures["available"] == food_cell["available"]


def test_get_budget_view_row_ids_carry_labels(isolated):
    """Opaque ids never ship bare — every row's id rides alongside its own label."""
    body, _ = call("get_budget_view", period="2026-06")
    for g in body["groups"]:
        assert g["id"] and g["label"]
        for row in g["rows"]:
            assert row["id"] and row["label"]


def test_get_budget_view_unknown_engine_returns_named_error(isolated):
    body, proposed = call("get_budget_view", engine_id="fifty_thirty_twenty")
    assert proposed is None
    assert "error" in body
    assert "fifty_thirty_twenty" in body["error"]
    assert "list_budget_engines" in body["error"]


# ── draft_budget_engine_config ──────────────────────────────────────────────────────


def test_draft_budget_engine_config_zero_based_proposes_targets(isolated):
    """Zero-based proposes monthly targets now (owner, 2026-08-23) — it used to report
    "nothing to propose", which was only ever true of a book that already budgets."""
    body, proposed = call("draft_budget_engine_config", engine_id="zero_based")
    assert proposed is None
    assert "error" not in body
    assert body["engine_id"] == "zero_based"
    assert body["has_proposal"] is True
    assert isinstance(body["proposed_state"]["targets"], list)
    assert all(isinstance(r["suggested_amount"], float) for r in body["proposed_state"]["rows"])


def test_draft_budget_engine_config_applies_nothing(isolated):
    """A draft is a preview only — calling it must not touch zero-based state."""
    before, _ = call("get_budget_view", period="2026-06")
    call("draft_budget_engine_config", engine_id="zero_based")
    after, _ = call("get_budget_view", period="2026-06")
    assert before == after


def test_draft_budget_engine_config_unknown_engine_returns_named_error(isolated):
    body, proposed = call("draft_budget_engine_config", engine_id="caps")
    assert proposed is None
    assert "error" in body
    assert "caps" in body["error"]
    assert "list_budget_engines" in body["error"]


def test_draft_budget_engine_config_missing_engine_id_errors(isolated):
    body, proposed = call("draft_budget_engine_config")
    assert proposed is None
    assert "error" in body


# ── proportional, via the tool surface (self-sufficient shared/history) ────────────
#
# This tool surface passes shared={}/history={} to every engine (see module docstring) —
# ProportionalEngine must compute its own transactions/rates/categories/spend-history in
# that case. These tests are the regression net for that self-sufficiency, exercised through
# the exact call path Lucius uses.


def test_get_budget_view_proportional_computes_a_real_view(isolated):
    body, proposed = call("get_budget_view", engine_id="proportional", period="2026-06")
    assert proposed is None
    assert body["engine_id"] == "proportional"
    assert body["period"] == "2026-06"
    assert body["base_currency"] == "SGD"
    assert body["rows_shape"] == "proportion-bars"
    # Groups are the book's own Zero-Based groups now — whatever they resolve to — always
    # ending in the one fixed synthetic group, Savings.
    assert body["groups"][-1]["id"] == "savings"
    assert len(body["groups"]) >= 1
    assert body["headline"]["unit"] == "percent"


def test_draft_budget_engine_config_proportional_proposes_group_targets(isolated, monkeypatch):
    """draft() now proposes each GROUP's target share of income (3-month average spend),
    not a per-category needs/wants/skip guess — the keyword heuristic is gone along with
    the per-envelope bucket model. Needs an income figure on file, unlike the old
    category-label heuristic which needed none."""
    from backend.services import profile as profile_service

    monkeypatch.setattr(profile_service, "read_profile", lambda: {"monthly_net_income": 4000.0})

    body, proposed = call("draft_budget_engine_config", engine_id="proportional")
    assert proposed is None
    assert body["has_proposal"] is True
    proposal = body["proposed_state"]
    assert isinstance(proposal["group_targets"], dict)
    assert isinstance(proposal["rows"], list) and proposal["rows"]
    assert all({"group_id", "name", "avg_spend", "percent"} <= set(r) for r in proposal["rows"])


def test_draft_budget_engine_config_proportional_has_no_proposal_without_income(isolated):
    """The default profile has no monthly_net_income — draft() honestly reports nothing
    to propose a SHARE of, rather than fabricating a split against zero income."""
    body, proposed = call("draft_budget_engine_config", engine_id="proportional")
    assert proposed is None
    assert body["has_proposal"] is False


def test_get_budget_view_proportional_carries_cap_activity_and_income(isolated):
    """The coordinator's parity requirement: Lucius's own read (get_budget_view) must show
    the SAME per-envelope cap/activity and month income the screen (GET
    /api/planning/proportional/month) shows — via the generic ViewModel's rows[].measures
    and the savings group's own 'income' measure, since a paradigm-neutral ViewModel has
    no per-engine-shaped field for either."""
    from backend.db.category_store import CategoryStore
    from backend.models.category_defs import BUILTIN_CATEGORIES

    CategoryStore().bulk_insert(BUILTIN_CATEGORIES)
    food_env = _env("cat_13095ba70000")

    call(
        "apply_budget_operation",
        engine_id="proportional",
        operation="configure",
        params={},
    )
    call(
        "apply_budget_operation",
        engine_id="proportional",
        operation="set_cap",
        params={"envelope_id": food_env, "month": "2099-09", "amount": 400},
    )

    body, _ = call("get_budget_view", engine_id="proportional", period="2099-09")
    food_row = next(r for g in body["groups"] for r in g["rows"] if r["id"] == food_env)
    measures = {m["key"]: m for m in food_row["measures"]}
    assert measures["cap"]["amount_base"] == 400.0
    assert "activity" in measures and "left" in measures

    savings = next(g for g in body["groups"] if g["id"] == "savings")
    income_measure = next(m for m in savings["subtotal"] if m["key"] == "income")
    assert income_measure["amount_base"] is not None

    # The income SOURCE (profile vs. actual receipts) rides in the headline's help text —
    # the one place a paradigm-neutral ViewModel can carry a qualitative note.
    assert "income" in body["headline"]["help"].lower()


# ── apply_budget_operation (Phase 3, RFC Decision F; apply_budget_verb until 2026-09-26) ─────────────────────────────────────


def test_apply_budget_operation_zero_based_assign_lands_through_apply_budget_change(isolated):
    body, proposed = call(
        "apply_budget_operation",
        engine_id="zero_based",
        operation="assign",
        params={"month": "2026-06", "envelope_id": _env("cat_13095ba70000"), "amount": 800},
    )
    assert proposed is None
    assert body == {
        "ok": True,
        "engine_id": "zero_based",
        "operation": "assign",
        "result": {
            "ok": True,
            "month": "2026-06",
            "envelope_id": _env("cat_13095ba70000"),
            "assigned": 800.0,
        },
    }
    table, _ = call("get_budget_month", month="2026-06")
    row = next(
        c
        for g in table["groups"]
        for c in g["categories"]
        if c["envelope_id"] == _env("cat_13095ba70000")
    )
    assert row["assigned"] == 800.0


def test_apply_budget_operation_proportional_configure_marks_it_set_up(isolated):
    """configure no longer seeds anything on envelopes (no more heuristic bucket-guessing)
    — it's pure group-target validation. Calling it with no params still marks the method
    configured, the setup screen's minimum "start budgeting" action."""
    from backend.db.category_store import CategoryStore
    from backend.models.category_defs import BUILTIN_CATEGORIES

    CategoryStore().bulk_insert(BUILTIN_CATEGORIES)

    body, proposed = call(
        "apply_budget_operation", engine_id="proportional", operation="configure", params={}
    )
    assert proposed is None
    assert body["ok"] is True
    assert body["result"] == {"group_targets": []}  # nothing named yet — none was set

    # And it's really the book's engine state now — a later view reflects it.
    listed, _ = call("list_budget_engines")
    assert next(e for e in listed["engines"] if e["id"] == "proportional")["configured"] is True


def test_apply_budget_operation_proportional_configure_with_group_targets(isolated):
    from backend.db.category_store import CategoryStore
    from backend.models.category_defs import BUILTIN_CATEGORIES

    CategoryStore().bulk_insert(BUILTIN_CATEGORIES)
    bills = _create_group("Bills")

    body, proposed = call(
        "apply_budget_operation",
        engine_id="proportional",
        operation="configure",
        params={"group_targets": {bills: 55}},
    )
    assert proposed is None
    assert body["result"] == {
        "group_targets": [{"group": {"id": bills, "name": "Bills"}, "percent": 55.0}]
    }


def test_apply_budget_operation_proportional_set_group_targets_names_the_group(isolated):
    from backend.db.category_store import CategoryStore
    from backend.models.category_defs import BUILTIN_CATEGORIES

    CategoryStore().bulk_insert(BUILTIN_CATEGORIES)
    bills = _create_group("Bills")

    body, _ = call(
        "apply_budget_operation",
        engine_id="proportional",
        operation="set_group_targets",
        params={"targets": {bills: 40}},
    )
    assert body == {
        "ok": True,
        "engine_id": "proportional",
        "operation": "set_group_targets",
        "result": {"group_targets": [{"group": {"id": bills, "name": "Bills"}, "percent": 40.0}]},
    }


def test_apply_budget_operation_proportional_set_group_excluded_names_the_group(isolated):
    from backend.db.category_store import CategoryStore
    from backend.models.category_defs import BUILTIN_CATEGORIES

    CategoryStore().bulk_insert(BUILTIN_CATEGORIES)
    bills = _create_group("Bills")

    body, _ = call(
        "apply_budget_operation",
        engine_id="proportional",
        operation="set_group_excluded",
        params={"group_id": bills, "excluded": True},
    )
    assert body == {
        "ok": True,
        "engine_id": "proportional",
        "operation": "set_group_excluded",
        "result": {"excluded_groups": [{"id": bills, "name": "Bills"}]},
    }


def test_apply_budget_operation_proportional_set_cap_applies_from_month_onward(isolated):
    from backend.db.category_store import CategoryStore
    from backend.models.category_defs import BUILTIN_CATEGORIES

    CategoryStore().bulk_insert(BUILTIN_CATEGORIES)
    food_env = _env("cat_13095ba70000")

    body, _ = call(
        "apply_budget_operation",
        engine_id="proportional",
        operation="set_cap",
        params={"envelope_id": food_env, "month": "2099-09", "amount": 450},
    )
    assert body == {
        "ok": True,
        "engine_id": "proportional",
        "operation": "set_cap",
        "result": {
            "caps": [
                {
                    "envelope": {"id": food_env, "name": _name(food_env)},
                    "cap_history": [["2099-09", 450.0]],
                }
            ]
        },
    }


def test_apply_budget_operation_proportional_set_cap_refuses_a_past_month(isolated):
    from backend.db.category_store import CategoryStore
    from backend.models.category_defs import BUILTIN_CATEGORIES

    CategoryStore().bulk_insert(BUILTIN_CATEGORIES)
    food_env = _env("cat_13095ba70000")

    body, proposed = call(
        "apply_budget_operation",
        engine_id="proportional",
        operation="set_cap",
        params={"envelope_id": food_env, "month": "2020-01", "amount": 100},
    )
    assert proposed is None
    assert body["ok"] is False
    assert "past months are history" in body["error"]


def test_apply_budget_operation_unknown_engine_returns_named_error(isolated):
    body, proposed = call(
        "apply_budget_operation", engine_id="fifty_thirty_twenty", operation="assign", params={}
    )
    assert proposed is None
    assert "error" in body
    assert "fifty_thirty_twenty" in body["error"]
    assert "list_budget_engines" in body["error"]


def test_apply_budget_operation_unknown_operation_names_the_real_ones(isolated):
    body, proposed = call(
        "apply_budget_operation", engine_id="zero_based", operation="asign", params={}
    )
    assert proposed is None
    assert "error" in body
    assert "asign" in body["error"]
    assert "'assign'" in body["error"]


def test_apply_budget_operation_on_the_removed_cash_envelope_engine_errors(isolated):
    body, proposed = call(
        "apply_budget_operation",
        engine_id="envelope",
        operation="set_pots",
        params={"pots": [{"envelope_id": _env("cat_13095ba70000"), "amount": 250}]},
    )
    assert proposed is None
    assert body["ok"] is False
    assert "envelope" in body["error"]
    assert "list_budget_engines" in body["error"]


def test_apply_budget_operation_missing_fields_errors(isolated):
    body, proposed = call("apply_budget_operation", engine_id="zero_based")
    assert proposed is None
    assert "error" in body


# ── set_home_budget_engine (Phase 2, RFC Decision A/E) ──────────────────────────────


def test_set_home_budget_engine_switches_and_reports_fresh_state(isolated):
    _seed_zero_based_history()
    # 50/30/20 must be configured before it can be home (set_home_engine's guard).
    call(
        "apply_budget_operation",
        engine_id="proportional",
        operation="configure",
        params={},
    )
    body, proposed = call("set_home_budget_engine", engine_id="proportional")
    assert proposed is None
    assert body["ok"] is True
    assert body["home_engine_id"] == "proportional"
    by_id = {e["id"]: e for e in body["engines"]}
    assert by_id["proportional"]["is_home"] is True
    assert by_id["zero_based"]["is_home"] is False

    # And it really persisted.
    listed, _ = call("list_budget_engines")
    assert listed["home_engine_id"] == "proportional"


def test_set_home_budget_engine_unknown_id_returns_named_error(isolated):
    _seed_zero_based_history()
    body, proposed = call("set_home_budget_engine", engine_id="fifty_thirty_twenty")
    assert proposed is None
    assert body["ok"] is False
    assert "fifty_thirty_twenty" in body["error"]
    assert "list_budget_engines" in body["error"]
    # A rejected switch must leave the home lens untouched.
    listed, _ = call("list_budget_engines")
    assert listed["home_engine_id"] == "zero_based"


def test_set_home_budget_engine_missing_id_errors(isolated):
    _seed_zero_based_history()
    body, proposed = call("set_home_budget_engine")
    assert proposed is None
    assert body["ok"] is False


def test_set_home_budget_engine_refuses_unconfigured_proportional(isolated):
    """The book must never land on 50/30/20 before it has ever been configured — an
    empty, all-"unsorted" method with no way back. The error names the fix
    (apply_budget_operation's configure operation) so Lucius can recover in one round trip."""
    _seed_zero_based_history()
    body, proposed = call("set_home_budget_engine", engine_id="proportional")
    assert proposed is None
    assert body["ok"] is False
    assert "configure" in body["error"]
    # A refused switch must leave the home lens untouched.
    listed, _ = call("list_budget_engines")
    assert listed["home_engine_id"] == "zero_based"


def test_set_home_budget_engine_allows_proportional_once_configured(isolated):
    from backend.db.category_store import CategoryStore
    from backend.models.category_defs import BUILTIN_CATEGORIES

    CategoryStore().bulk_insert(BUILTIN_CATEGORIES)
    call(
        "apply_budget_operation",
        engine_id="proportional",
        operation="configure",
        params={},
    )
    body, proposed = call("set_home_budget_engine", engine_id="proportional")
    assert proposed is None
    assert body["ok"] is True
    assert body["home_engine_id"] == "proportional"
