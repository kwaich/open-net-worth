"""POST /api/plan/outlook · /insights, GET /api/plan/scenario-odds — what Plan › Outlook reads.

The screen and ``simulate_retirement`` must read ONE answer. These pin that over HTTP: the same
request twice is the same JSON, an empty body is the saved plan, and it is exactly the figure
Lucius's tool reports.

Run:
    .venv/bin/python3 -m pytest tests/test_plan_outlook_api.py -q
"""

from __future__ import annotations

import asyncio
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.ai.tools import execute_tool
from backend.api.plan_outlook import router
from backend.db.profile_store import ProfileStore
from backend.services import montecarlo as mc
from backend.services import plan_state, retirement_plan

PROFILE = {
    "date_of_birth": "1982-03-10",
    "retirement_target_age": 62,
    "monthly_saving": 1_200,
    "monthly_net_income": 6_100,
    "monthly_expenses": 4_000,
    "fire_monthly_retirement_spending": 3_500,
    "fire_expected_return_pct": 6.0,
    "fire_life_expectancy": 90,
}


@pytest.fixture(autouse=True)
def book(monkeypatch):
    ProfileStore().set_raw(dict(PROFILE))
    monkeypatch.setattr(retirement_plan, "_current_age", lambda dob: 44.0 if dob else None)
    monkeypatch.setattr(retirement_plan.ledger_service, "base_currency", lambda: "SGD")
    monkeypatch.setattr(
        retirement_plan.ledger_service, "net_worth", lambda base=None: {"net_worth": 480_000.0}
    )
    mc._cache.clear()


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_the_same_request_twice_is_the_same_answer(client):
    first = client.post("/api/plan/outlook", json={"levers": {"retireAge": 60}}).json()
    mc._cache.clear()
    second = client.post("/api/plan/outlook", json={"levers": {"retireAge": 60}}).json()
    assert first == second


def test_an_empty_body_is_the_saved_plan(client):
    body = client.post("/api/plan/outlook", json={}).json()
    assert body["changed"] == []
    assert body["inputs"]["saving"] == 1_200  # the stated saving, not income − expenses
    assert body["inputs"]["retireAge"] == 62
    assert body["inputs"]["netWorth"] == 480_000
    assert body["result"]["n_paths"] == mc.FAN_PATHS
    assert body["result"]["outcomes"] is None
    assert len(body["result"]["bands"]) == body["result"]["horizon_years"] + 1


def test_lucius_and_the_screen_report_the_same_odds(client):
    screen = client.post("/api/plan/outlook", json={"levers": {}}).json()["result"]
    text, _ = asyncio.run(execute_tool("simulate_retirement", {}))
    lucius = json.loads(text)
    assert lucius["success_probability"] == screen["success_probability"]
    assert lucius["median_ending"] == screen["median_ending"]
    assert lucius["pot_at_retirement_median"] == screen["nest_egg_at_retire"]
    assert lucius["matches_the_outlook"] is True


def test_levers_move_only_what_they_name(client):
    body = client.post("/api/plan/outlook", json={"levers": {"saving": 2_000, "vol": 18}}).json()
    assert body["changed"] == ["saving", "vol"]
    assert body["inputs"]["saving"] == 2_000 and body["inputs"]["retireAge"] == 62


def test_dragging_asks_for_fewer_paths_and_rich_broke_dead_is_opt_in(client):
    quick = client.post("/api/plan/outlook", json={"quick": True, "outcomes": True}).json()
    assert quick["result"]["n_paths"] == mc.QUICK_PATHS
    assert len(quick["result"]["outcomes"]) == quick["result"]["horizon_years"] + 1


def test_an_out_of_range_lever_is_refused(client):
    assert client.post("/api/plan/outlook", json={"levers": {"retireAge": 500}}).status_code == 422
    assert client.post("/api/plan/outlook", json={"levers": {"swr": 0}}).status_code == 422


def test_no_birth_date_is_a_422_that_says_why(client):
    ProfileStore().set_raw({k: v for k, v in PROFILE.items() if k != "date_of_birth"})
    res = client.post("/api/plan/outlook", json={})
    assert res.status_code == 422
    assert "date of birth" in res.json()["detail"]


def test_insights_are_the_safe_age_and_the_savings_hint(client):
    body = client.post("/api/plan/outlook/insights", json={}).json()
    assert set(body) == {"earliest", "extra_save", "gap_save", "number_age"}


def test_scenario_odds_are_live_percentages_per_saved_scenario(client):
    created = plan_state.add_scenario({"name": "Later", "retireAge": 65, "odds": 1})
    odds = client.get("/api/plan/scenario-odds").json()["odds"]
    assert set(odds) == {created["id"]}
    assert 0 <= odds[created["id"]] <= 100 and odds[created["id"]] != 1
