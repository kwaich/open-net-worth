"""services/retirement_plan.py — the SAVED plan, and the one answer built from it.

The bug this exists for (persona QA 2026-10-13): the Outlook and Lucius were two engines on two
sets of defaults, so one question had two numbers. Here: the screen's call (levers = the saved
plan, however spelled) and Lucius's (no arguments) are the same inputs through the same engine,
a what-if names every lever it moved, and a saved scenario's live odds are that scenario run
through the same fan.

Offline: profile + plan baseline live in the test DB; net worth and the birth date are patched.

Run:
    .venv/bin/python3 -m pytest tests/test_retirement_plan.py -q
"""

from __future__ import annotations

from datetime import date

import pytest

from backend.core.errors import ValidationError
from backend.db.profile_store import ProfileStore
from backend.models.retirement import OutlookLevers
from backend.services import montecarlo as mc
from backend.services import plan_state, reports_rollup, retirement_plan

PROFILE = {
    "date_of_birth": "1982-03-10",
    "retirement_target_age": 62,
    "monthly_net_income": 6_100,
    "monthly_expenses": 4_000,
    "fire_monthly_retirement_spending": 3_500,
    "fire_expected_return_pct": 6.0,
    "fire_withdrawal_rate_pct": 4.0,
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
    yield
    mc._cache.clear()


def test_the_saved_plan_is_the_profile_fire_settings():
    plan = retirement_plan.saved_plan()
    assert plan["saving"] == 2_100  # income − expenses: no saving was ever stated
    assert plan["retireAge"] == 62
    assert plan["lifeExp"] == 90
    assert plan["monthlyRetirementSpending"] == 3_500
    assert plan["ret"] == 6.0
    assert plan["vol"] == retirement_plan.DEFAULT_VOLATILITY_PCT
    assert plan["streams"] == [] and plan["events"] == []


def test_a_stated_saving_is_the_plan_saving_whatever_income_and_spending_say():
    ProfileStore().set_raw({**PROFILE, "monthly_saving": 1_200})
    assert retirement_plan.saved_plan()["saving"] == 1_200


def test_the_saved_outlook_baseline_overrides_the_profile_and_carries_streams_and_events():
    plan_state.set_baseline(
        {
            "saving": 3_000,
            "retireAge": 60,
            "streams": [{"label": "CPF LIFE", "amount": 1_400, "age": 65}],
            "events": [{"id": "h", "label": "Home", "amount": -150_000, "age": 47}],
        }
    )
    plan = retirement_plan.saved_plan()
    assert plan["saving"] == 3_000 and plan["retireAge"] == 60
    assert plan["lifeExp"] == 90  # not in the baseline: the profile's
    assert plan["streams"][0]["amount"] == 1_400
    assert plan["events"][0]["amount"] == -150_000


def test_with_no_overrides_the_screen_and_lucius_get_the_same_numbers():
    screen = retirement_plan.outlook(OutlookLevers())
    lucius = retirement_plan.answer(None)
    assert lucius["result"] == screen["result"]
    assert lucius["changed"] == [] == screen["changed"]


def test_levers_that_spell_out_the_saved_plan_are_not_a_what_if():
    plan = retirement_plan.saved_plan()
    spelled_out = OutlookLevers(
        saving=plan["saving"], retireAge=plan["retireAge"], ret=plan["ret"], events=[]
    )
    answer = retirement_plan.answer(spelled_out)
    assert answer["changed"] == []
    assert answer["result"] == retirement_plan.answer(None)["result"]


def test_a_what_if_names_each_lever_it_moved_with_the_saved_value():
    answer = retirement_plan.answer(OutlookLevers(retireAge=58, saving=2_100, vol=20))
    assert answer["changed"] == ["vol", "retireAge"]
    assert answer["saved_plan_values"] == {
        "vol": retirement_plan.DEFAULT_VOLATILITY_PCT,
        "retireAge": 62,
    }
    assert answer["inputs"]["retireAge"] == 58
    assert (
        answer["result"]["success_probability"]
        < retirement_plan.answer(None)["result"]["success_probability"]
    )


def test_a_starting_pot_override_is_reported_as_changed():
    answer = retirement_plan.answer(OutlookLevers(startNW=400_000))
    assert answer["changed"] == ["startNW"]
    assert answer["inputs"]["startNW"] == 400_000 and answer["inputs"]["netWorth"] == 480_000


def test_a_cpf_life_payout_is_a_guaranteed_income_stream_that_lifts_the_odds():
    base = retirement_plan.answer(None)["result"]["success_probability"]
    cpf_life = OutlookLevers(streams=[{"label": "CPF LIFE", "amount": 1_800, "age": 65}])
    answer = retirement_plan.answer(cpf_life)
    assert answer["changed"] == ["streams"]
    assert answer["result"]["success_probability"] > base


def test_quick_and_full_runs_share_their_random_paths():
    quick = retirement_plan.outlook(OutlookLevers(), quick=True)["result"]
    full = retirement_plan.outlook(OutlookLevers())["result"]
    assert quick["n_paths"] == mc.QUICK_PATHS and full["n_paths"] == mc.FAN_PATHS
    assert abs(quick["success_probability"] - full["success_probability"]) < 0.05


def test_shock_windows_default_to_the_retirement_age_and_can_be_moved():
    flagged = retirement_plan.outlook(OutlookLevers(crash=True, weakSeq=True))
    assert flagged["changed"] == ["crash", "weakSeq"]
    moved = retirement_plan.outlook(
        OutlookLevers(crash=True, weakSeq=True, shockCfg={"crashAge": 50, "weakFrom": 50})
    )
    assert moved["changed"] == ["crash", "weakSeq", "shockCfg"]
    assert moved["result"]["success_probability"] != flagged["result"]["success_probability"]


def test_the_insights_are_the_screens_safe_age_and_savings_hint():
    insights = retirement_plan.insights(OutlookLevers())
    assert set(insights) == {"earliest", "extra_save", "gap_save", "number_age"}
    answer = retirement_plan.answer(None)
    assert answer["insights"] == insights


def test_without_a_birth_date_there_is_no_outlook_and_the_error_says_what_to_do():
    ProfileStore().set_raw({k: v for k, v in PROFILE.items() if k != "date_of_birth"})
    with pytest.raises(ValidationError, match="date of birth"):
        retirement_plan.outlook(OutlookLevers())


def test_a_saved_scenarios_live_odds_are_the_same_fan_the_headline_runs():
    created = plan_state.add_scenario(
        {"name": "Retire at 58", "retireAge": 58, "saving": 2_100, "events": []}
    )
    odds = retirement_plan.scenario_odds()
    headline = retirement_plan.outlook(OutlookLevers(retireAge=58))["result"]
    assert odds == {created["id"]: round(headline["success_probability"] * 100)}


def test_a_scenario_inherits_the_saved_plan_but_not_its_events():
    plan_state.set_baseline(
        {"events": [{"id": "h", "label": "Home", "amount": -300_000, "age": 47}]}
    )
    created = plan_state.add_scenario({"name": "Same plan", "retireAge": 62})
    with_home = retirement_plan.outlook(OutlookLevers())["result"]["success_probability"]
    without_home = retirement_plan.outlook(OutlookLevers(events=[]))["result"][
        "success_probability"
    ]
    assert with_home < without_home
    assert retirement_plan.scenario_odds()[created["id"]] == round(without_home * 100)


def _ledger_months(monkeypatch, months: dict[str, tuple[float, float]]):
    monkeypatch.setattr(reports_rollup, "user_today", lambda: date(2026, 10, 14))
    monkeypatch.setattr(
        reports_rollup,
        "month_summary",
        lambda ym: {
            "ym": ym,
            "income_total": months.get(ym, (0.0, 0.0))[0],
            "expense_total": months.get(ym, (0.0, 0.0))[1],
        },
    )


def test_the_ledgers_saving_is_the_last_complete_months_net_and_never_the_plans(monkeypatch):
    _ledger_months(
        monkeypatch,
        {
            "2026-07": (6_000, 2_700),
            "2026-08": (6_000, 2_700),
            "2026-09": (6_000, 2_700),
            "2026-10": (0, 40),  # the month in progress is not a whole month
        },
    )
    seen = reports_rollup.trailing_saving()
    assert seen == {
        "monthly": 3_300,
        "rate_pct": 55.0,
        "months": 3,
        "from": "2026-07",
        "to": "2026-09",
        "base_currency": "SGD",
    }
    assert retirement_plan.saved_plan()["saving"] == 2_100  # reading the ledger changes nothing


def test_a_month_with_nothing_in_it_is_not_averaged_in_and_two_are_enough(monkeypatch):
    _ledger_months(monkeypatch, {"2026-08": (5_000, 2_000), "2026-09": (5_000, 3_000)})
    seen = reports_rollup.trailing_saving()
    assert seen is not None and seen["monthly"] == 2_500 and seen["months"] == 2


def test_one_active_month_is_too_little_to_call_it_what_you_save(monkeypatch):
    _ledger_months(monkeypatch, {"2026-09": (5_000, 2_000)})
    assert reports_rollup.trailing_saving() is None
