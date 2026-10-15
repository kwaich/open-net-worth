"""The FIRE Settings form shows the volatility and life expectancy the plan is using. Those two
defaults are written in the frontend (``fireSettings.ts``) because the profile is where the form
reads from; this keeps them equal to the ones the engine actually falls back to."""

from __future__ import annotations

import re
from pathlib import Path

from backend.services import fire, retirement_plan

SOURCE = (
    Path(__file__).resolve().parent.parent / "frontend/src/components/tabs/plan/fireSettings.ts"
)


def _constant(name: str) -> float:
    match = re.search(rf"export const {name} = ([\d.]+)", SOURCE.read_text())
    assert match, f"{name} not found in fireSettings.ts"
    return float(match.group(1))


def test_the_form_shows_the_volatility_the_plan_falls_back_to():
    assert _constant("FIRE_VOLATILITY_IN_USE") == retirement_plan.DEFAULT_VOLATILITY_PCT


def test_the_form_shows_the_life_expectancy_the_plan_falls_back_to():
    assert _constant("FIRE_LIFE_EXPECTANCY_IN_USE") == fire.LIFE_EXPECTANCY
