"""A monthly saving is its OWN stored fact (ADR 2026-10-14-one-retirement-engine-saving-is-a-fact).

It used to be derived as ``monthly_net_income − monthly_expenses`` and WRITTEN back as income, so
"I save 1,200" stored an income of 1,200 and a later real income/spending silently rewrote the
plan. These pin the contract: the field round-trips, no income/spending edit moves it, and a
profile that never stated one still derives it as before.

Run:
    .venv/bin/python3 -m pytest tests/test_monthly_saving.py -q
"""

from __future__ import annotations

import asyncio
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.ai.tools import execute_tool
from backend.api.profile import router as profile_router
from backend.core.errors import ValidationError
from backend.db.profile_store import ProfileStore
from backend.services.fire import derive_fire_inputs, monthly_saving
from backend.services.profile import read_profile, write_profile


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(profile_router)
    return TestClient(app)


def test_a_profile_that_never_stated_a_saving_derives_it_from_income_and_spending():
    profile = {"monthly_net_income": 6100, "monthly_expenses": 4000}
    assert monthly_saving(profile) == 2100
    assert derive_fire_inputs(profile)["monthly_savings"] == 2100


def test_a_stated_saving_wins_over_income_minus_spending():
    profile = {"monthly_saving": 1200, "monthly_net_income": 6100, "monthly_expenses": 4000}
    assert monthly_saving(profile) == 1200
    assert derive_fire_inputs(profile)["monthly_savings"] == 1200


def test_a_stated_zero_is_a_real_answer_not_unset():
    profile = {"monthly_saving": 0, "monthly_net_income": 6100, "monthly_expenses": 4000}
    assert monthly_saving(profile) == 0


def test_the_what_if_override_still_beats_a_stated_saving():
    profile = {"monthly_saving": 1200}
    assert derive_fire_inputs(profile, {"monthly_savings": 500})["monthly_savings"] == 500


def test_saving_round_trips_without_touching_income(client):
    put = client.put("/api/profile", json={"monthly_saving": 1200})
    assert put.status_code == 200

    stored = client.get("/api/profile").json()
    assert stored["monthly_saving"] == 1200
    assert stored["monthly_net_income"] is None


def test_income_and_spending_edits_never_move_a_stated_saving(client):
    client.put("/api/profile", json={"monthly_saving": 1200})

    client.put("/api/profile", json={"monthly_net_income": 6100, "monthly_expenses": 4000})

    stored = client.get("/api/profile").json()
    assert stored["monthly_saving"] == 1200
    assert derive_fire_inputs(read_profile())["monthly_savings"] == 1200


def test_lucius_saving_income_and_spending_leaves_a_stated_saving_alone():
    ProfileStore().set_raw({"monthly_saving": 1200})

    asyncio.run(
        execute_tool(
            "update_profile",
            {"changes": {"monthly_net_income": 6100, "monthly_expenses": 4000}},
        )
    )

    assert read_profile()["monthly_saving"] == 1200


def test_lucius_can_state_a_saving_without_inventing_an_income():
    text, _ = asyncio.run(execute_tool("update_profile", {"changes": {"monthly_saving": "1200"}}))

    assert json.loads(text)["ok"] is True
    stored = read_profile()
    assert stored["monthly_saving"] == 1200.0
    assert stored["monthly_net_income"] is None


@pytest.mark.parametrize("value", ["NaN", "Infinity"])
def test_a_non_finite_saving_is_refused_over_http(client, value):
    assert client.put("/api/profile", json={"monthly_saving": value}).status_code == 422
    assert read_profile()["monthly_saving"] is None


def test_the_write_choke_refuses_a_non_finite_saving_from_any_caller():
    with pytest.raises(ValidationError):
        write_profile({"monthly_saving": float("inf")})


def test_an_overspender_can_state_a_negative_saving_and_save_the_whole_fire_plan(client):
    """Spending more than they earn is a real fact. "Update my FIRE plan" sends the derived
    (negative) saving with six other FIRE fields in ONE put — a 422 on the saving would fail all."""
    from backend.services import plan_state

    body = {
        "monthly_saving": -350,
        "fire_monthly_retirement_spending": 3_000,
        "fire_withdrawal_rate_pct": 4,
        "fire_expected_return_pct": 6,
        "fire_volatility_pct": 15,
        "retirement_target_age": 62,
        "fire_life_expectancy": 90,
    }
    assert client.put("/api/profile", json=body).status_code == 200
    stored = client.get("/api/profile").json()
    assert stored["monthly_saving"] == -350 and stored["retirement_target_age"] == 62
    assert derive_fire_inputs(read_profile())["monthly_savings"] == -350
    assert plan_state.set_baseline({"saving": -350, "retireAge": 62})["saving"] == -350
    assert asyncio.run(execute_tool("update_profile", {"changes": {"monthly_saving": -400}}))
    assert read_profile()["monthly_saving"] == -400.0


# ── a stated saving is not hidden by an older "Update my FIRE plan" ───────────────────────
# The saved plan is the profile overlaid with the Outlook baseline, so a baseline `saving` left
# from an earlier save would silently beat a saving the user states afterwards — in chat, with
# the Outlook closed. A profile edit hands its setting back; an agreeing or unrelated write
# (the Outlook's own save, a timezone capture) leaves the baseline alone.

BASELINE = {"saving": 4_000, "ret": 6, "retireAge": 60, "streams": [], "events": []}


def _saved_saving() -> float:
    from backend.services import retirement_plan

    return retirement_plan.saved_plan()["saving"]


def test_a_stated_saving_replaces_the_baselines_older_copy():
    from backend.services import plan_state

    plan_state.set_baseline(dict(BASELINE))
    assert _saved_saving() == 4_000

    asyncio.run(execute_tool("update_profile", {"changes": {"monthly_saving": 1_500}}))

    assert _saved_saving() == 1_500
    kept = plan_state.get_baseline()
    assert "saving" not in kept
    assert kept["ret"] == 6 and kept["retireAge"] == 60  # the rest of the plan is untouched


def test_the_outlooks_own_save_does_not_look_like_an_outside_edit(client):
    from backend.services import plan_state

    plan_state.set_baseline(dict(BASELINE))

    client.put("/api/profile", json={"monthly_saving": 4_000, "retirement_target_age": 60})

    assert plan_state.get_baseline()["saving"] == 4_000
    assert plan_state.get_baseline()["retireAge"] == 60


def test_an_unrelated_profile_write_leaves_the_baseline_alone(client):
    from backend.services import plan_state

    plan_state.set_baseline(dict(BASELINE))

    client.put("/api/profile", json={"notes": "hello"})
    client.put("/api/profile", json={"monthly_expenses": 3_000})

    baseline = plan_state.get_baseline()
    assert baseline["saving"] == 4_000 and baseline["ret"] == 6


def test_releasing_the_last_lever_clears_the_baseline():
    from backend.services import plan_state

    plan_state.set_baseline({"saving": 4_000})
    asyncio.run(execute_tool("update_profile", {"changes": {"monthly_saving": 1_500}}))
    assert plan_state.get_baseline() is None
