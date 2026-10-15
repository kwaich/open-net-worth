"""
montecarlo — THE retirement simulation. One engine for Plan › Outlook and Lucius.

The Outlook used to run its own Monte Carlo in the browser while ``simulate_retirement`` ran
this module's older year-offset one, so the same question had two answers (persona QA
2026-10-13: screen 57% / 73%, Lucius 52% / 94% / 81%). Both now call THIS file, through
``services.retirement_plan`` (ADR 2026-10-14-one-retirement-engine-saving-is-a-fact).

The model, in monthly steps over the whole life, in TODAY's money (real returns):
  * you SAVE ``monthly_saving`` each month until ``retire_age``, then contributions stop and
    you DRAW ``monthly_spend`` minus any guaranteed income that has started (CPF LIFE, a
    pension, rent — ``Stream``s, which only ever offset the drawdown);
  * each month the pot grows by a normal return, ``real_return_pct/12`` mean and
    ``volatility_pct/sqrt(12)`` deviation, then saving or drawing, then any one-off ``Event``
    (signed like a ledger amount: negative = spend, positive = windfall);
  * market shocks are windows of AGE: a weak-returns / lost-decade window swaps the mean for a
    depressed real return, an inflation window shaves it, and a crash cuts the pot once;
  * a path is RUINED the first time it would go below zero, and stays ruined — success is the
    share of paths never ruined that still hold money at life expectancy.

Reproducibility (the no-ad-hoc-randomness rule): every draw comes from ONE
``numpy.random.default_rng(FAN_SEED)`` laid out as (path, month), so identical inputs give
identical output on one install (NumPy does not promise a Generator's stream across releases,
so an upgrade can move a headline by a point or so). A smaller ``n_paths`` is exactly the first
rows of a larger one — the quick (dragging) fan, the full fan and the insight scans all read
the same paths. The scans share random numbers across their candidates too, so "retire at 58 vs
59" differs only by the retire age.

Pure and compute-only: stdlib + numpy, no store, no FX. The caller owns resolving the
inputs; this module only simulates them.
"""

from __future__ import annotations

import math
import threading
from collections import OrderedDict
from dataclasses import dataclass, replace
from functools import lru_cache
from typing import Any, Callable, Optional

import numpy as np

MONTHS_PER_YEAR = 12
MAX_HORIZON_YEARS = 100

FAN_PATHS = 5000  # the answer: what the screen settles on and what Lucius quotes
QUICK_PATHS = 1400  # while a slider is being dragged — the first rows of the same paths
SCAN_PATHS = 1000  # the insight scans (earliest stop, saving gap, FIRE-number age)
FAN_SEED = 777

# Shock shapes. A "weak" or "lost" window replaces the mean REAL return; an inflation window
# subtracts from it every month it lasts.
WEAK_REAL_RETURN_PCT = -1.5
INFLATION_SHOCK_PCT = 3.0

_BAND_PERCENTILES = (10, 25, 50, 75, 90)
_CACHE_MAX = 64
_DRAWS_CACHE_MAX = 4  # distinct (months, n_paths) shapes: ~a user's horizon × fan/quick/scan
_cache: OrderedDict[tuple, dict] = OrderedDict()
_cache_lock = threading.Lock()  # FastAPI runs sync routes on a thread pool


@dataclass(frozen=True)
class Stream:
    """Guaranteed income, base currency per month, from ``age`` until ``end_age`` (exclusive;
    None = for life)."""

    amount: float
    age: float
    end_age: Optional[float] = None


@dataclass(frozen=True)
class Event:
    """A one-off cash flow at ``age``: negative = spend, positive = windfall."""

    age: float
    amount: float


@dataclass(frozen=True)
class Window:
    """Ages ``start`` up to (not including) ``end``; ``end`` None = open-ended."""

    start: float
    end: Optional[float] = None


@dataclass(frozen=True)
class LifeInputs:
    start_balance: float
    current_age: float
    retire_age: float
    life_expectancy: float
    real_return_pct: float
    volatility_pct: float
    monthly_saving: float
    monthly_spend: float
    streams: tuple[Stream, ...] = ()
    events: tuple[Event, ...] = ()
    crash_fraction: float = 0.0
    crash_age: Optional[float] = None
    weak: Optional[Window] = None
    lost: Optional[Window] = None
    inflation_shock: Optional[Window] = None


@dataclass(frozen=True)
class _Schedule:
    months: int
    drift: np.ndarray  # mean monthly return per month, shocks applied
    guaranteed: np.ndarray  # guaranteed income per month
    lump: np.ndarray  # one-off events per month
    crash_month: int  # 1-based month the crash lands, or -1


def _round_half_up(x: float) -> int:
    return int(math.floor(x + 0.5))


def horizon_years(inp: LifeInputs) -> int:
    return int(
        min(MAX_HORIZON_YEARS, max(1, _round_half_up(inp.life_expectancy - inp.current_age)))
    )


def _in_window(ages: np.ndarray, w: Optional[Window]) -> np.ndarray:
    if w is None:
        return np.zeros(ages.shape, dtype=bool)
    inside = ages >= w.start
    return inside if w.end is None else inside & (ages < w.end)


def _schedule(inp: LifeInputs, years: int) -> _Schedule:
    months = years * MONTHS_PER_YEAR
    ages = inp.current_age + np.arange(1, months + 1) / MONTHS_PER_YEAR
    normal = inp.real_return_pct / 100 / MONTHS_PER_YEAR
    weak = WEAK_REAL_RETURN_PCT / 100 / MONTHS_PER_YEAR
    drift = np.where(_in_window(ages, inp.weak) | _in_window(ages, inp.lost), weak, normal)
    drift = drift - np.where(
        _in_window(ages, inp.inflation_shock), INFLATION_SHOCK_PCT / 100 / MONTHS_PER_YEAR, 0.0
    )
    guaranteed = np.zeros(months)
    for s in inp.streams:
        guaranteed += s.amount * (ages >= s.age) * (1 if s.end_age is None else ages < s.end_age)
    lump = np.zeros(months)
    for e in inp.events:
        m = _round_half_up((e.age - inp.current_age) * MONTHS_PER_YEAR)
        if 1 <= m <= months:
            lump[m - 1] += e.amount
    crash_month = -1
    if inp.crash_fraction > 0:
        at = inp.crash_age if inp.crash_age is not None else inp.retire_age
        crash_month = _round_half_up((at - inp.current_age) * MONTHS_PER_YEAR)
    return _Schedule(months, drift, guaranteed, lump, crash_month)


@lru_cache(maxsize=_DRAWS_CACHE_MAX)
def _draws(months: int, n_paths: int) -> np.ndarray:
    """Standard normals as (month, path), laid out (path, month) first so the first ``n``
    paths are the same whatever ``n_paths`` is. They depend only on the shape and the fixed
    seed, and generating them is most of an uncached request (up to ~50 MB at the longest
    horizon), so the few most recent shapes are kept — read-only, they are shared."""
    raw = np.random.default_rng(FAN_SEED).standard_normal((n_paths, months))
    draws = np.ascontiguousarray(raw.T)
    draws.setflags(write=False)
    return draws


def _run(
    inp: LifeInputs,
    sched: _Schedule,
    z: np.ndarray,
    savings: np.ndarray,
    retire_months: np.ndarray,
    *,
    keep_years: bool,
) -> tuple[np.ndarray, np.ndarray]:
    """Simulate ``len(savings)`` candidate plans over the SAME ``z`` draws at once. Returns the
    survived mask (candidate, path) and the end-of-year pots (year, candidate, path) — empty
    unless ``keep_years``."""
    k, n_paths = savings.size, z.shape[1]
    sigma = inp.volatility_pct / 100 / math.sqrt(MONTHS_PER_YEAR)
    pot = np.full((k, n_paths), float(inp.start_balance))
    ruined = np.zeros((k, n_paths), dtype=bool)
    yearly = np.empty((sched.months // MONTHS_PER_YEAR + 1 if keep_years else 0, k, n_paths))
    if keep_years:
        yearly[0] = pot
    for i in range(sched.months):
        month = i + 1
        pot *= 1.0 + sched.drift[i] + sigma * z[i]
        if month == sched.crash_month:
            pot *= 1.0 - inp.crash_fraction
        draw = max(0.0, inp.monthly_spend - sched.guaranteed[i])
        pot += np.where(month <= retire_months, savings, -draw)[:, None]
        if sched.lump[i]:
            pot += sched.lump[i]
        ruined |= pot < 0.0
        np.maximum(pot, 0.0, out=pot)
        if keep_years and month % MONTHS_PER_YEAR == 0:
            yearly[month // MONTHS_PER_YEAR] = pot
    return ~ruined & (pot > 0.0), yearly


def _retire_months(inp: LifeInputs, ages: np.ndarray, months: int) -> np.ndarray:
    return np.clip((ages - inp.current_age) * MONTHS_PER_YEAR, 0, months)


def _band(values: np.ndarray, age: float, year: int) -> dict:
    p10, p25, p50, p75, p90 = (float(v) for v in np.percentile(values, _BAND_PERCENTILES))
    return {
        "year": year,
        "age": age,
        "p10": round(p10, 2),
        "p25": round(p25, 2),
        "p50": round(p50, 2),
        "p75": round(p75, 2),
        "p90": round(p90, 2),
    }


def survival_curve(current_age: float, median_death: float) -> Callable[[float], float]:
    """P(alive at age a | alive now): an illustrative logistic mortality curve whose median
    death is the plan's life expectancy — used only to overlay mortality on Rich/Broke/Dead."""
    median = max(median_death or 90, 80)

    def raw(age: float) -> float:
        return 1 / (1 + math.exp((age - median) / 6.5))

    base = raw(current_age) or 1

    def survival(age: float) -> float:
        return max(0.0, min(1.0, raw(age) / base))

    return survival


def _outcomes(yearly: np.ndarray, inp: LifeInputs, rich_base: float) -> list[dict]:
    """Per age: the share Dead (mortality), Broke (pot gone), or still rich in three tiers
    (<2x, 2-5x, >5x ``rich_base``) — plus the same split conditional on being alive."""
    survive = survival_curve(inp.current_age, inp.life_expectancy)
    rows = []
    for y in range(yearly.shape[0]):
        pots = yearly[y]
        cond = {
            "broke": float(np.mean(pots <= 1)),
            "rich1": float(np.mean((pots > 1) & (pots < 2 * rich_base))),
            "rich2": float(np.mean((pots >= 2 * rich_base) & (pots < 5 * rich_base))),
            "rich3": float(np.mean(pots >= 5 * rich_base)),
        }
        age = inp.current_age + y
        s = survive(age)
        rows.append(
            {
                "age": age,
                "alive": s,
                "dead": 1 - s,
                "broke": cond["broke"] * s,
                "rich1": cond["rich1"] * s,
                "rich2": cond["rich2"] * s,
                "rich3": cond["rich3"] * s,
                "cond": cond,
            }
        )
    return rows


def simulate_life(
    inp: LifeInputs, *, n_paths: int = FAN_PATHS, outcomes: bool = False
) -> dict[str, Any]:
    """The fan: per-year percentile bands, the odds the money lasts to life expectancy, the pot
    at the retirement line, and (``outcomes=True``) the Rich/Broke/Dead split per age. Cached —
    treat the result as read-only."""
    key = (inp, n_paths, outcomes)
    with _cache_lock:
        if key in _cache:
            _cache.move_to_end(key)
            return _cache[key]
    years = horizon_years(inp)
    sched = _schedule(inp, years)
    survived, yearly = _run(
        inp,
        sched,
        _draws(sched.months, n_paths),
        np.array([inp.monthly_saving], dtype=float),
        _retire_months(inp, np.array([inp.retire_age]), sched.months),
        keep_years=True,
    )
    pots = yearly[:, 0, :]
    bands = [_band(pots[y], inp.current_age + y, y) for y in range(years + 1)]
    success = float(np.mean(survived[0]))
    retire_year = max(0, min(years, _round_half_up(inp.retire_age - inp.current_age)))
    at_retire, at_end = bands[retire_year], bands[-1]
    result: dict[str, Any] = {
        "bands": bands,
        "retire_year": retire_year,
        "horizon_years": years,
        "n_paths": n_paths,
        "success_probability": success,
        "depletion_probability": 1 - success,
        "nest_egg_at_retire": at_retire["p50"],
        "nest_egg_band": at_retire,
        "median_ending": at_end["p50"],
        "p10_ending": at_end["p10"],
        "p90_ending": at_end["p90"],
        "outcomes": (
            _outcomes(pots, inp, at_retire["p50"] or inp.start_balance) if outcomes else None
        ),
    }
    with _cache_lock:
        _cache[key] = result
        while len(_cache) > _CACHE_MAX:
            _cache.popitem(last=False)
    return result


def _first_meeting(odds: np.ndarray, bar: float) -> Optional[int]:
    hit = np.flatnonzero(odds >= bar)
    return int(hit[0]) if hit.size else None


def earliest_stop(inp: LifeInputs, bar: float) -> Optional[dict]:
    """The earliest whole-year-later retire age (from two years out to five before life
    expectancy) whose odds reach ``bar`` — saving until then, drawing after."""
    ages = (
        inp.current_age
        + 2
        + np.arange(max(0, math.floor(inp.life_expectancy - 5 - (inp.current_age + 2)) + 1))
    )
    if ages.size == 0:
        return None
    sched = _schedule(inp, horizon_years(inp))
    survived, _ = _run(
        inp,
        sched,
        _draws(sched.months, SCAN_PATHS),
        np.full(ages.size, float(inp.monthly_saving)),
        _retire_months(inp, ages, sched.months),
        keep_years=False,
    )
    odds = survived.mean(axis=1)
    i = _first_meeting(odds, bar)
    return None if i is None else {"age": float(ages[i]), "odds": float(odds[i])}


def extra_saving_for(
    inp: LifeInputs, bar: float, max_add: float, step: float = 250.0
) -> Optional[float]:
    """The smallest extra monthly saving, in ``step`` increments up to ``max_add``, that lifts
    the odds to ``bar`` at the plan's retire age. 0 = already there; None = not within reach."""
    adds = np.arange(0.0, max_add + 1e-9, step)
    sched = _schedule(inp, horizon_years(inp))
    survived, _ = _run(
        inp,
        sched,
        _draws(sched.months, SCAN_PATHS),
        inp.monthly_saving + adds,
        _retire_months(inp, np.full(adds.size, inp.retire_age), sched.months),
        keep_years=False,
    )
    i = _first_meeting(survived.mean(axis=1), bar)
    return None if i is None else float(adds[i])


def age_pot_reaches(inp: LifeInputs, target: float) -> Optional[float]:
    """The age the typical (median) path first holds ``target`` if the user never stops saving."""
    fan = simulate_life(replace(inp, retire_age=inp.life_expectancy), n_paths=SCAN_PATHS)
    return next((b["age"] for b in fan["bands"] if b["p50"] >= target), None)
