"""
AI tool-dispatch tests for plan_scenarios: list/create/update/delete scenarios
and get/set baseline.

Driven via execute_tool (asyncio.run). The PlanStateStore is re-pointed at
tmp_path so tests are fully isolated. Offline — no LLM, no network.

Run:
    .venv/bin/python3 -m pytest tests/test_tools_plan_scenarios.py -v
"""

from __future__ import annotations

import asyncio
import json

import pytest

from backend.ai.tools import TOOL_SCHEMAS, execute_tool


def call(tool_name, **inp):
    text, proposed = asyncio.run(execute_tool(tool_name, inp))
    return json.loads(text), proposed


@pytest.fixture
def isolated(db_isolated, monkeypatch):
    from backend.db.plan_state_store import PlanStateStore
    from backend.services import plan_state as svc

    store = PlanStateStore()
    monkeypatch.setattr(svc, "_store", store)
    return store


# ── registry: all 6 tool names are present ────────────────────────────────────


def test_plan_scenario_tools_registered():
    names = {t["name"] for t in TOOL_SCHEMAS}
    assert {
        "list_plan_scenarios",
        "create_plan_scenario",
        "update_plan_scenario",
        "delete_plan_scenario",
        "get_plan_baseline",
        "set_plan_baseline",
    } <= names


# ── list → create → update → delete round-trip ────────────────────────────────


def test_list_empty(isolated):
    res, proposed = call("list_plan_scenarios")
    assert proposed is None
    assert res["scenarios"] == []


def test_create_scenario(isolated):
    res, proposed = call(
        "create_plan_scenario",
        name="Optimistic",
        ret=8.0,
        vol=15.0,
        swr=4.0,
        monthlyRetirementSpending=3000,
    )
    assert proposed is None
    assert res["ok"] is True
    scen = res["scenario"]
    assert scen["name"] == "Optimistic"
    assert scen["ret"] == 8.0
    assert "id" in scen
    assert scen["monthlyRetirementSpending"] == 3000


def test_create_scenario_rejects_retired_spend_key(isolated):
    res, _ = call("create_plan_scenario", name="Legacy", spend=36_000)
    assert res["ok"] is False
    assert "monthlyRetirementSpending" in res["error"]


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -1])
def test_create_scenario_rejects_invalid_monthly_retirement_spending_without_writing(
    isolated, value
):
    res, _ = call(
        "create_plan_scenario",
        name="Invalid",
        monthlyRetirementSpending=value,
    )

    assert res["ok"] is False
    assert isolated.list_scenarios() == []


def test_list_after_create(isolated):
    call("create_plan_scenario", name="A", ret=7.0)
    call("create_plan_scenario", name="B", ret=5.0)
    res, _ = call("list_plan_scenarios")
    assert len(res["scenarios"]) == 2
    names = {s["name"] for s in res["scenarios"]}
    assert names == {"A", "B"}


def test_update_scenario(isolated):
    created, _ = call("create_plan_scenario", name="Base", ret=7.0, swr=4.0)
    sid = created["scenario"]["id"]
    res, proposed = call("update_plan_scenario", scenario_id=sid, name="Revised", ret=6.0, swr=3.5)
    assert proposed is None
    assert res["ok"] is True
    assert res["scenario"]["name"] == "Revised"
    assert res["scenario"]["ret"] == 6.0
    assert res["scenario"]["id"] == sid


def test_update_scenario_not_found(isolated):
    res, proposed = call("update_plan_scenario", scenario_id="ghost-id", name="X")
    assert proposed is None
    assert res["ok"] is False
    assert "not found" in res["error"].lower()


def test_delete_scenario(isolated):
    created, _ = call("create_plan_scenario", name="ToDelete")
    sid = created["scenario"]["id"]
    res, proposed = call("delete_plan_scenario", scenario_id=sid)
    assert proposed is None
    assert res["ok"] is True
    assert res["deleted_id"] == sid
    # Confirm gone
    listed, _ = call("list_plan_scenarios")
    assert listed["scenarios"] == []


def test_delete_scenario_not_found(isolated):
    res, proposed = call("delete_plan_scenario", scenario_id="does-not-exist")
    assert proposed is None
    assert res["ok"] is False
    assert "not found" in res["error"].lower()


def test_create_scenario_missing_name_rejected(isolated):
    res, proposed = call("create_plan_scenario", ret=7.0)
    assert proposed is None
    assert res["ok"] is False
    assert "name" in res["error"].lower()


def test_create_scenario_lever_out_of_range_rejected(isolated):
    res, proposed = call("create_plan_scenario", name="Bad", ret=150.0)
    assert proposed is None
    assert res["ok"] is False
    assert "ret" in res["error"]


# ── get/set baseline round-trip ────────────────────────────────────────────────


def test_get_baseline_initially_null(isolated):
    res, proposed = call("get_plan_baseline")
    assert proposed is None
    assert res["baseline"] is None


def test_set_and_get_baseline(isolated):
    res, proposed = call(
        "set_plan_baseline",
        baseline={"ret": 7.0, "swr": 3.5, "monthlyRetirementSpending": 4000},
    )
    assert proposed is None
    assert res["ok"] is True
    assert res["baseline"]["ret"] == 7.0

    fetched, _ = call("get_plan_baseline")
    assert fetched["baseline"]["swr"] == 3.5
    assert fetched["baseline"]["monthlyRetirementSpending"] == 4000


def test_clear_baseline(isolated):
    call("set_plan_baseline", baseline={"ret": 7.0})
    res, proposed = call("set_plan_baseline", baseline=None)
    assert proposed is None
    assert res["ok"] is True
    assert res["baseline"] is None

    fetched, _ = call("get_plan_baseline")
    assert fetched["baseline"] is None


def test_set_baseline_lever_validation(isolated):
    res, proposed = call("set_plan_baseline", baseline={"swr": 200.0})
    assert proposed is None
    assert res["ok"] is False
    assert "swr" in res["error"]


def test_set_baseline_empty_dict_clears(isolated):
    call("set_plan_baseline", baseline={"ret": 6.0})
    # An empty dict body means "clear"
    res, _ = call("set_plan_baseline", baseline={})
    assert res["ok"] is True
    assert res["baseline"] is None
