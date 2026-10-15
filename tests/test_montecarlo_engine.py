"""The ONE retirement simulation (services/montecarlo.py) — pure engine, no profile, no ledger.

Pins what the Outlook and ``simulate_retirement`` both depend on: identical inputs give
identical numbers, a smaller path count is the first rows of a larger one, and every modelled
effect (saving, drawdown, guaranteed income, one-off events, market shocks, the insight scans)
moves the odds the way its name says.

Run:
    .venv/bin/python3 -m pytest tests/test_montecarlo_engine.py -q
"""

from __future__ import annotations

from dataclasses import replace

import numpy as np
import pytest

from backend.services import montecarlo as mc

PLAN = mc.LifeInputs(
    start_balance=500_000,
    current_age=40.0,
    retire_age=60.0,
    life_expectancy=90.0,
    real_return_pct=4.0,
    volatility_pct=14.0,
    monthly_saving=2_000,
    monthly_spend=4_000,
)


@pytest.fixture(autouse=True)
def _fresh_cache():
    mc._cache.clear()
    yield
    mc._cache.clear()


def _odds(inp: mc.LifeInputs, n: int = mc.QUICK_PATHS) -> float:
    return mc.simulate_life(inp, n_paths=n)["success_probability"]


def test_identical_inputs_give_identical_numbers_even_without_the_cache():
    first = mc.simulate_life(PLAN)
    mc._cache.clear()
    assert mc.simulate_life(PLAN) == first


def test_equal_inputs_are_one_cache_entry():
    assert mc.simulate_life(PLAN) is mc.simulate_life(replace(PLAN))


def test_a_smaller_path_count_is_the_first_rows_of_a_larger_one():
    full = mc._draws(24, mc.FAN_PATHS)
    quick = mc._draws(24, mc.QUICK_PATHS)
    assert np.array_equal(quick, full[:, : mc.QUICK_PATHS])


def test_bands_cover_every_year_from_the_starting_pot_and_stay_ordered():
    r = mc.simulate_life(PLAN)
    assert len(r["bands"]) == 51
    assert r["bands"][0]["p10"] == r["bands"][0]["p90"] == PLAN.start_balance
    assert r["bands"][0]["age"] == 40.0
    for b in r["bands"]:
        assert b["p10"] <= b["p25"] <= b["p50"] <= b["p75"] <= b["p90"]


def test_zero_volatility_is_a_single_deterministic_path():
    r = mc.simulate_life(replace(PLAN, volatility_pct=0.0))
    assert r["success_probability"] in (0.0, 1.0)
    assert all(b["p10"] == b["p90"] for b in r["bands"])


def test_a_pot_that_cannot_fund_retirement_is_ruined_and_stays_ruined():
    r = mc.simulate_life(replace(PLAN, start_balance=0, monthly_saving=0))
    assert r["success_probability"] == 0.0
    assert r["median_ending"] == 0.0


def test_saving_more_raises_the_odds():
    assert (
        _odds(replace(PLAN, monthly_saving=4_000))
        > _odds(PLAN)
        > _odds(replace(PLAN, monthly_saving=0))
    )


def test_spending_more_in_retirement_lowers_the_odds():
    assert _odds(replace(PLAN, monthly_spend=6_000)) < _odds(PLAN)


def test_retiring_later_raises_the_odds():
    assert _odds(replace(PLAN, retire_age=65)) > _odds(PLAN) > _odds(replace(PLAN, retire_age=55))


def test_guaranteed_income_from_retirement_raises_the_odds():
    cpf_life = mc.Stream(amount=1_500, age=65)
    assert _odds(replace(PLAN, streams=(cpf_life,))) > _odds(PLAN)


def test_a_stream_ending_before_it_pays_nothing_and_a_stream_before_retirement_is_ignored():
    assert _odds(replace(PLAN, streams=(mc.Stream(2_000, 45, 55),))) == _odds(PLAN)
    assert _odds(replace(PLAN, streams=(mc.Stream(2_000, 65, 65),))) == _odds(PLAN)


def test_a_stream_that_ends_helps_less_than_one_for_life():
    through_80 = _odds(replace(PLAN, streams=(mc.Stream(1_500, 65, 80),)))
    for_life = _odds(replace(PLAN, streams=(mc.Stream(1_500, 65),)))
    assert _odds(PLAN) < through_80 < for_life


def test_events_are_signed_like_a_ledger_spend_negative_windfall_positive():
    house = _odds(replace(PLAN, events=(mc.Event(age=45, amount=-150_000),)))
    inheritance = _odds(replace(PLAN, events=(mc.Event(age=45, amount=150_000),)))
    assert house < _odds(PLAN) < inheritance


def test_an_event_past_life_expectancy_changes_nothing():
    assert _odds(replace(PLAN, events=(mc.Event(age=95, amount=-1e6),))) == _odds(PLAN)


def test_each_market_shock_lowers_the_odds():
    base = _odds(PLAN)
    assert _odds(replace(PLAN, crash_fraction=0.3, crash_age=60)) < base
    assert _odds(replace(PLAN, weak=mc.Window(60, 70))) < base
    assert _odds(replace(PLAN, lost=mc.Window(40, 50))) < base
    assert _odds(replace(PLAN, inflation_shock=mc.Window(40))) < base


def test_a_crash_with_no_age_lands_at_retirement():
    assert _odds(replace(PLAN, crash_fraction=0.3)) == _odds(
        replace(PLAN, crash_fraction=0.3, crash_age=PLAN.retire_age)
    )


def test_a_fractional_age_from_a_birth_date_does_not_break_the_fan():
    r = mc.simulate_life(replace(PLAN, current_age=28.1, retire_age=62.0))
    assert r["retire_year"] == 34
    assert r["nest_egg_at_retire"] == r["bands"][34]["p50"]
    assert len(r["bands"]) == r["horizon_years"] + 1


def test_retirement_spending_is_a_monthly_amount_end_to_end():
    one_year = mc.LifeInputs(
        start_balance=120_000,
        current_age=60.0,
        retire_age=60.0,
        life_expectancy=61.0,
        real_return_pct=0.0,
        volatility_pct=0.0,
        monthly_saving=0,
        monthly_spend=1_000,
    )
    assert mc.simulate_life(one_year)["median_ending"] == 108_000
    broke = mc.simulate_life(replace(one_year, start_balance=12_000), outcomes=True)
    assert broke["outcomes"][-1]["cond"]["broke"] == 1


def test_the_draws_cache_changes_nothing_and_hands_out_read_only_arrays():
    mc._draws.cache_clear()
    cold = mc.simulate_life(PLAN, n_paths=mc.QUICK_PATHS)
    stop_cold = mc.earliest_stop(PLAN, 0.8)
    mc._cache.clear()
    assert mc._draws.cache_info().hits > 0 or mc._draws.cache_info().currsize > 0
    assert mc.simulate_life(PLAN, n_paths=mc.QUICK_PATHS) == cold
    assert mc.earliest_stop(PLAN, 0.8) == stop_cold
    assert mc._draws(24, 50) is mc._draws(24, 50)
    with pytest.raises(ValueError):
        mc._draws(24, 50)[0, 0] = 1.0
    uncached = mc._draws.__wrapped__(24, 50)
    assert np.array_equal(uncached, mc._draws(24, 50))


def test_the_horizon_is_clamped():
    assert mc.horizon_years(replace(PLAN, life_expectancy=30.0)) == 1
    assert mc.horizon_years(replace(PLAN, life_expectancy=500.0)) == mc.MAX_HORIZON_YEARS


def test_the_rich_broke_dead_split_always_adds_up():
    rows = mc.simulate_life(PLAN, outcomes=True)["outcomes"]
    assert len(rows) == 51
    for row in rows:
        total = row["dead"] + row["broke"] + row["rich1"] + row["rich2"] + row["rich3"]
        assert total == pytest.approx(1.0)
        assert sum(row["cond"].values()) == pytest.approx(1.0)


def test_outcomes_are_only_computed_when_asked_for():
    assert mc.simulate_life(PLAN)["outcomes"] is None


def test_earliest_stop_reports_odds_the_fan_agrees_with():
    stop = mc.earliest_stop(PLAN, 0.8)
    assert stop is not None
    again = mc.simulate_life(replace(PLAN, retire_age=stop["age"]), n_paths=mc.SCAN_PATHS)
    assert again["success_probability"] == stop["odds"] >= 0.8


def test_earliest_stop_is_none_when_no_age_gets_there():
    assert mc.earliest_stop(replace(PLAN, start_balance=0, monthly_saving=0), 0.8) is None


def test_extra_saving_lifts_the_plan_to_the_bar_and_not_a_step_sooner():
    bar = 0.9
    add = mc.extra_saving_for(PLAN, bar, 8_000)
    assert add is not None and add > 0
    lifted = replace(PLAN, monthly_saving=PLAN.monthly_saving + add)
    assert _odds(lifted, mc.SCAN_PATHS) >= bar
    assert _odds(replace(lifted, monthly_saving=lifted.monthly_saving - 250), mc.SCAN_PATHS) < bar


def test_extra_saving_is_zero_when_the_plan_is_already_there_and_none_when_out_of_reach():
    assert mc.extra_saving_for(replace(PLAN, monthly_saving=6_000), 0.8, 4_000) == 0.0
    assert mc.extra_saving_for(replace(PLAN, monthly_spend=200_000), 0.9, 1_000) is None


def test_the_age_the_pot_reaches_a_target_is_when_the_median_path_first_holds_it():
    age = mc.age_pot_reaches(PLAN, 1_000_000)
    assert age is not None and PLAN.current_age < age < PLAN.life_expectancy
    assert mc.age_pot_reaches(PLAN, 1e12) is None
