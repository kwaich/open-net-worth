"""
AI tool-dispatch tests for the savings-goal tools (P2-BU1; renamed from ``*_target(s)`` and the
spending-limit setters retired 2026-09-26 — ADR 2026-09-26-budget-and-category-tools).

Offline + isolated: TargetsStore is DB-backed (plan 4a task 3) — db_isolated gives each
test its own fresh, migrated database; profile path monkeypatched into tmp_path.
execute_tool is async, driven via asyncio.run. No LLM, no network. Mirrors
test_commitments_tools.py.

Run:
    .venv/bin/pytest tests/test_goals_tools.py -v
"""

from __future__ import annotations

import asyncio
import json

import pytest

from backend.ai.tools import TOOL_SCHEMAS, execute_tool
from backend.db.targets_store import TargetsStore

# A renamed tool name no longer resolves at runtime (ADR 2026-08-25-tool-names-are-final) —
# the migration's rename table is the only record that a rename happened.
from backend.services import targets as ts


@pytest.fixture
def isolated(tmp_path, monkeypatch, db_isolated):
    monkeypatch.setattr(ts, "targets_store", TargetsStore())
    # budgets read/write hit the DB-backed ProfileStore (Wave 5, JSON->DB) — db_isolated
    # already gives it a fresh, empty row, no monkeypatch needed.
    monkeypatch.setattr(ts.svc, "base_currency", lambda: "SGD")
    monkeypatch.setattr(ts, "load_fx_cache", lambda: {"SGD": 1.0})
    monkeypatch.setattr(ts, "_TODAY", "2026-06-04")
    return tmp_path


def call(tool, **inp):
    text, proposed = asyncio.run(execute_tool(tool, inp))
    return json.loads(text), proposed


def test_goal_tools_registered():
    names = {t["name"] for t in TOOL_SCHEMAS}
    assert {
        "get_goals",
        "get_goal_allocation",
        "create_goal",
        "update_goal",
        "delete_goal",
    } <= names


def test_retired_and_renamed_names_are_gone_and_recorded():
    """A tool name is persisted data, so a rename is recorded, never aliased (ADR
    2026-08-25-tool-names-are-final). The flat per-category spending limits had no screen and
    fed no figure the user sees; the savings tools said "target", which also named Zero-Based's
    per-envelope target and the investment target mix."""
    from backend.scripts.tool_renames import RENAMES

    names = {t["name"] for t in TOOL_SCHEMAS}
    renamed = {
        "get_targets": "get_goals",
        "get_target_allocation": "get_goal_allocation",
        "create_target": "create_goal",
        "update_target": "update_goal",
        "delete_target": "delete_goal",
    }
    for old, new in renamed.items():
        assert old not in names and RENAMES[old] == new
    for retired in (
        "set_spending_limits",
        "set_spending_limit_rollovers",
        "set_spending_limit_rollover_start",
    ):
        assert retired not in names and RENAMES[retired].startswith("retired")
    assert "copy_targets_to_caps" in RENAMES["set_spending_limits"]


def test_a_retired_name_does_not_dispatch():
    text, proposed = asyncio.run(execute_tool("set_spending_limits", {"budgets": {"food": 1}}))
    assert proposed is None
    assert "set_spending_limits" in text and "unknown" in text.lower()


# ── goal tools ─────────────────────────────────────────────────────────────


def test_create_get_update_delete_goal(isolated):
    created, proposed = call(
        "create_goal",
        name="Emergency fund",
        target_amount=12000,
        current_amount=2000,
        target_date="2026-12-04",
    )
    assert created["ok"] and proposed is None  # direct write, not propose-tier
    tid = created["goal"]["id"]
    assert created["goal"]["remaining_base"] == 10000.0
    assert created["goal"]["months_remaining"] == 6

    listed, _ = call("get_goals")
    assert any(t["id"] == tid for t in listed)

    updated, _ = call("update_goal", goal_id=tid, current_amount=5000)
    assert updated["ok"] and updated["goal"]["remaining_base"] == 7000.0

    deleted, _ = call("delete_goal", goal_id=tid)
    assert deleted["ok"] is True
    assert call("get_goals")[0] == []


def test_create_goal_rejects_bad_amount(isolated):
    res, _ = call("create_goal", name="X", target_amount=0, target_date="2026-12-31")
    assert res["ok"] is False and "greater than 0" in res["error"]


def test_update_goal_missing(isolated):
    res, _ = call("update_goal", goal_id="tgt-nope", name="x")
    assert res["ok"] is False


def _accounts(*names):
    from backend.db.account_store import AccountStore

    return [
        AccountStore().create({"name": n, "type": "cash", "currency": "SGD"})["id"] for n in names
    ]


def test_create_goal_with_linked_account_ids(isolated):
    a1, a2 = _accounts("EF one", "EF two")
    created, proposed = call(
        "create_goal",
        name="EF",
        target_amount=10000,
        target_date="2026-12-04",
        linked_account_ids=[a1, a2, a1],
    )
    assert created["ok"] and proposed is None
    assert created["goal"]["linked_account_ids"] == [a1, a2]  # cleaned


def test_create_goal_refuses_an_unknown_linked_account(isolated):
    created, _ = call(
        "create_goal",
        name="EF",
        target_amount=10000,
        target_date="2026-12-04",
        linked_account_ids=["acc-gone"],
    )
    assert created["ok"] is False
    assert "acc-gone" in created["error"]


def test_update_goal_replaces_linked_account_ids(isolated):
    a1, a8, a9 = _accounts("EF one", "EF eight", "EF nine")
    created, _ = call(
        "create_goal",
        name="EF",
        target_amount=10000,
        target_date="2026-12-04",
        linked_account_ids=[a1],
    )
    tid = created["goal"]["id"]
    updated, _ = call("update_goal", goal_id=tid, linked_account_ids=[a9, a8])
    assert updated["ok"]
    assert updated["goal"]["linked_account_ids"] == [a9, a8]


def test_update_goal_no_op_when_no_changeable_fields(isolated):
    # An id-only call (no name/target_amount/current_amount/... field) can't change anything —
    # must report no_op honestly instead of the pre-fix behavior of echoing the unchanged row
    # back as a fake "ok" success (the update_transactions incident, generalized).
    created, _ = call(
        "create_goal",
        name="EF",
        target_amount=10000,
        current_amount=2000,
        target_date="2026-12-04",
    )
    tid = created["goal"]["id"]

    result, proposed = call("update_goal", goal_id=tid)

    assert proposed is None
    assert result["ok"] is True
    assert result["no_op"] is True
    assert result["goal"]["id"] == tid
    assert result["goal"]["current_amount"] == 2000  # unchanged, just echoed

    # nothing was actually written to the store
    stored = ts.targets_store.find_by_id(tid)
    assert stored["current_amount"] == 2000
    assert stored["name"] == "EF"


def test_update_goal_missing_with_no_fields_stays_not_found(isolated):
    # A missing id keeps its not-found error — the no_op guard never masks that.
    res, _ = call("update_goal", goal_id="tgt-nope")
    assert res["ok"] is False
    assert "no_op" not in res


def test_update_profile_ignores_goals_key(isolated, monkeypatch):
    # update_profile no longer supports goals; passing one must not error, and it must not
    # resurface in the stored profile (goals are Targets now — P2-GO1). read_profile/write_profile
    # (used by the tool) resolve through the DB-backed ProfileStore — db_isolated already
    # isolates it, no monkeypatch needed.
    from backend.services import profile as profile_mod

    res, proposed = call(
        "update_profile", changes={"name": "Tester", "goals": [{"name": "x", "target_amount": 5}]}
    )
    assert res["ok"] and proposed is None
    # re-read through the API read path strips goals
    assert "goals" not in profile_mod.read_profile()
    assert profile_mod.read_profile()["name"] == "Tester"


def test_get_goal_allocation_compute_only(isolated, monkeypatch):
    monkeypatch.setattr(ts.cashflow, "cash_balance_base", lambda base, *_a, **_k: 10000.0)
    monkeypatch.setattr(
        ts, "_read_profile", lambda: {"monthly_net_income": 5000, "monthly_expenses": 4000}
    )
    call("create_goal", name="EF", target_amount=6000, target_date="2026-12-04")
    res, _ = call("get_goal_allocation")
    assert res["total_required_monthly_base"] == 1000.0
    assert res["monthly_free_base"] == 1000.0
    assert res["free_after_targets_base"] == 0.0
