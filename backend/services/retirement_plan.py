"""
retirement_plan — the Outlook's inputs, resolved ONCE, for the screen and for Lucius alike.

``services.montecarlo`` is the only simulation; this module decides what goes into it. The
SAVED plan is the profile's FIRE settings (``services.fire.derive_fire_inputs``, so a stated
monthly saving beats income − expenses) overlaid with the Outlook baseline the user saved
("Update my FIRE plan": levers, guaranteed income streams, life events). A caller passes only
the levers it wants to CHANGE; the rest is the saved plan, so:

  * the screen with untouched levers and ``simulate_retirement`` with no arguments run the
    same inputs through the same engine on the same random numbers — identical numbers;
  * a what-if reports exactly which levers it moved off the saved plan (``changed``).

Compute-only: reads the profile, the plan baseline and live net worth; writes nothing.
Net worth is read here, never trusted from a caller, so the starting pot cannot disagree.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Optional

from backend.core.errors import ValidationError
from backend.models.retirement import OutlookLevers
from backend.services import ledger as ledger_service
from backend.services import montecarlo as mc
from backend.services import plan_state
from backend.services.fire import _current_age, _load_profile, derive_fire_inputs, fire_number

# expectedReturn is NOMINAL ("before inflation"); the engine works in today's money.
INFLATION_PCT = 2.3
DEFAULT_VOLATILITY_PCT = 15.0
CRASH_FRACTION = 0.3

SAFE_BAR = 0.8  # the "safe to retire" age: the earliest with at least this chance
LIFT_BAR = 0.9  # "+X a month would lift this to 90%"
LIFT_MAX_ADD = 4000.0
GAP_MAX_ADD = 8000.0

_VALUE_KEYS = (
    "saving",
    "ret",
    "vol",
    "monthlyRetirementSpending",
    "swr",
    "lifeExp",
    "retireAge",
    "streams",
    "events",
)
_FLAG_KEYS = ("crash", "weakSeq", "lostDec", "highInfl")
_PLAN_KEYS = _VALUE_KEYS + _FLAG_KEYS + ("shockCfg",)


@dataclass(frozen=True)
class _Book:
    """What the plan is measured against: who they are and what they hold today."""

    saved: dict[str, Any]
    current_age: float
    net_worth: float
    base_currency: str


@dataclass(frozen=True)
class _Resolved:
    plan: dict[str, Any]  # every lever, resolved
    changed: tuple[str, ...]  # the levers the caller moved off the saved plan
    book: _Book
    start: float
    inputs: mc.LifeInputs


def _round_half_up(x: float) -> int:
    return int(math.floor(x + 0.5))


def _saved_plan(profile: dict[str, Any]) -> dict[str, Any]:
    derived = derive_fire_inputs(profile)
    plan: dict[str, Any] = {
        "saving": derived["monthly_savings"],
        "ret": derived["expected_return_pct"],
        "vol": float(profile.get("fire_volatility_pct") or DEFAULT_VOLATILITY_PCT),
        "monthlyRetirementSpending": derived["monthly_retirement_spending"],
        "swr": derived["withdrawal_rate_pct"],
        "lifeExp": derived["life_expectancy"],
        "retireAge": derived["retirement_target_age"],
        "streams": [],
        "events": [],
        "crash": False,
        "weakSeq": False,
        "lostDec": False,
        "highInfl": False,
        "shockCfg": {},
    }
    baseline = plan_state.get_baseline() or {}
    for key in _VALUE_KEYS:
        value = baseline.get(key)
        if key in ("streams", "events"):
            if isinstance(value, list):
                plan[key] = value
        elif value is not None:
            plan[key] = value
    return plan


def saved_plan() -> dict[str, Any]:
    """The plan as the user saved it: profile FIRE settings, then the Outlook baseline."""
    return _saved_plan(_load_profile())


def _comparable(value: Any) -> Any:
    """A lever in a form two spellings of the same value share: labels and ids are display
    only, an absent field equals a null one, and 7 equals 7.0."""
    if isinstance(value, list):
        return [_comparable(item) for item in value]
    if isinstance(value, dict):
        return tuple(
            sorted(
                (k, _comparable(v))
                for k, v in value.items()
                if v is not None and k not in ("label", "id")
            )
        )
    if isinstance(value, bool) or value is None:
        return value
    return round(float(value), 6)


def _windows(plan: dict[str, Any], current_age: float) -> dict[str, Optional[mc.Window]]:
    cfg = plan.get("shockCfg") or {}
    retire, life = float(plan["retireAge"]), float(plan["lifeExp"])

    def pick(key: str, default: float) -> float:
        return default if cfg.get(key) is None else float(cfg[key])

    return {
        "crash": mc.Window(pick("crashAge", retire)) if plan["crash"] else None,
        "weak": (
            mc.Window(pick("weakFrom", retire), pick("weakTo", min(life, retire + 10)))
            if plan["weakSeq"]
            else None
        ),
        "lost": (
            mc.Window(pick("lostFrom", current_age), pick("lostTo", min(life, current_age + 10)))
            if plan["lostDec"]
            else None
        ),
        "inflation": (
            mc.Window(pick("inflFrom", current_age), pick("inflTo", life))
            if plan["highInfl"]
            else None
        ),
    }


def _life_inputs(plan: dict[str, Any], current_age: float, start: float) -> mc.LifeInputs:
    windows = _windows(plan, current_age)
    crash = windows["crash"]
    return mc.LifeInputs(
        start_balance=start,
        current_age=current_age,
        retire_age=float(plan["retireAge"]),
        life_expectancy=float(plan["lifeExp"]),
        real_return_pct=float(plan["ret"]) - INFLATION_PCT,
        volatility_pct=float(plan["vol"]),
        monthly_saving=float(plan["saving"]),
        monthly_spend=float(plan["monthlyRetirementSpending"]),
        streams=tuple(
            mc.Stream(
                float(s["amount"]),
                float(s["age"]),
                None if s.get("endAge") is None else float(s["endAge"]),
            )
            for s in plan["streams"]
        ),
        events=tuple(mc.Event(float(e["age"]), float(e["amount"])) for e in plan["events"]),
        crash_fraction=CRASH_FRACTION if crash else 0.0,
        crash_age=crash.start if crash else None,
        weak=windows["weak"],
        lost=windows["lost"],
        inflation_shock=windows["inflation"],
    )


def _read_book() -> _Book:
    profile = _load_profile()
    current_age = _current_age(profile.get("date_of_birth") or "")
    if current_age is None:
        raise ValidationError(
            "The retirement outlook needs a date of birth — ask for it and save it "
            "(update_profile date_of_birth) before simulating."
        )
    base = ledger_service.base_currency()
    return _Book(
        saved=_saved_plan(profile),
        current_age=current_age,
        net_worth=float(ledger_service.net_worth(base).get("net_worth", 0.0)),
        base_currency=base,
    )


def _resolve(supplied: dict[str, Any], book: Optional[_Book] = None) -> _Resolved:
    book = book or _read_book()
    plan = {**book.saved, **{k: v for k, v in supplied.items() if k in _PLAN_KEYS}}
    start = float(supplied["startNW"]) if supplied.get("startNW") is not None else book.net_worth
    changed = [
        key
        for key in _PLAN_KEYS
        if key in supplied and _comparable(supplied[key]) != _comparable(book.saved[key])
    ]
    if start != book.net_worth:
        changed.append("startNW")
    return _Resolved(
        plan=plan,
        changed=tuple(changed),
        book=book,
        start=start,
        inputs=_life_inputs(plan, book.current_age, start),
    )


def _supplied(levers: Optional[OutlookLevers]) -> dict[str, Any]:
    return levers.model_dump(exclude_none=True) if levers else {}


def _fire_number(plan: dict[str, Any]) -> int:
    return _round_half_up(fire_number(float(plan["monthlyRetirementSpending"]), float(plan["swr"])))


def _echo(r: _Resolved) -> dict[str, Any]:
    return {
        **r.plan,
        "startNW": r.start,
        "netWorth": r.book.net_worth,
        "currentAge": r.book.current_age,
        "inflation": INFLATION_PCT,
        "baseCurrency": r.book.base_currency,
        "fireNumber": _fire_number(r.plan),
    }


def outlook(
    levers: Optional[OutlookLevers] = None, *, quick: bool = False, outcomes: bool = False
) -> dict[str, Any]:
    """The fan for these levers over the saved plan. ``quick`` runs the first rows of the same
    random paths (a slider is being dragged); ``outcomes`` adds the Rich/Broke/Dead split."""
    r = _resolve(_supplied(levers))
    result = mc.simulate_life(
        r.inputs, n_paths=mc.QUICK_PATHS if quick else mc.FAN_PATHS, outcomes=outcomes
    )
    return {"inputs": _echo(r), "changed": list(r.changed), "result": result}


def insights(levers: Optional[OutlookLevers] = None) -> dict[str, Any]:
    """The slower reads: the safe-to-retire age, what extra saving would lift the odds, and
    the age the typical path reaches the FIRE number."""
    r = _resolve(_supplied(levers))
    return _insights(r)


def _insights(r: _Resolved) -> dict[str, Any]:
    earliest = mc.earliest_stop(r.inputs, SAFE_BAR)
    behind = earliest is None or r.inputs.retire_age - earliest["age"] < 0
    return {
        "earliest": earliest,
        "extra_save": mc.extra_saving_for(r.inputs, LIFT_BAR, LIFT_MAX_ADD),
        "gap_save": mc.extra_saving_for(r.inputs, SAFE_BAR, GAP_MAX_ADD) if behind else None,
        "number_age": mc.age_pot_reaches(r.inputs, _fire_number(r.plan)),
    }


def answer(levers: Optional[OutlookLevers] = None) -> dict[str, Any]:
    """The whole retirement answer in one call (what ``simulate_retirement`` returns): the
    fan, the insights, the resolved inputs, and the saved value of every lever moved."""
    r = _resolve(_supplied(levers))
    return {
        "inputs": _echo(r),
        "changed": list(r.changed),
        "saved_plan_values": {k: r.book.saved[k] for k in r.changed if k in r.book.saved},
        "result": mc.simulate_life(r.inputs),
        "insights": _insights(r),
    }


def _scenario_levers(scenario: dict[str, Any], saved: dict[str, Any]) -> dict[str, Any]:
    """The levers a saved scenario sets when recalled: a lever it leaves null is the saved
    plan's; a scenario without events means NO events (the baseline's are not inherited)."""
    levers: dict[str, Any] = {
        key: scenario[key] if scenario.get(key) is not None else saved[key]
        for key in ("saving", "ret", "vol", "monthlyRetirementSpending", "swr", "lifeExp")
    }
    levers["retireAge"] = (
        scenario["retireAge"] if scenario.get("retireAge") is not None else saved["retireAge"]
    )
    levers["streams"] = (
        scenario["streams"] if isinstance(scenario.get("streams"), list) else saved["streams"]
    )
    levers["events"] = scenario["events"] if isinstance(scenario.get("events"), list) else []
    for flag in _FLAG_KEYS:
        levers[flag] = bool(scenario.get(flag))
    levers["shockCfg"] = scenario.get("shockCfg") or {}
    return levers


def scenario_odds() -> dict[str, int]:
    """Live odds (whole percent) for every saved scenario, from the same engine and paths as
    the headline — a stored odds figure is a save-time snapshot that goes stale."""
    book = _read_book()
    return {
        scenario["id"]: _round_half_up(
            mc.simulate_life(_resolve(_scenario_levers(scenario, book.saved), book).inputs)[
                "success_probability"
            ]
            * 100
        )
        for scenario in plan_state.get_scenarios()
    }
