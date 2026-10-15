"""The demo book's ESSENTIAL day-to-day spending — food, transport, subscriptions, health.

Everything discretionary rides the Sapphire card, which is then paid in full each month —
the pattern that makes the credit-card payment envelope and the statement cycle mean
something. Bills, health, giving and tax come straight out of checking.

Amounts drift upward across the timeline (~2.7%/yr) so the spending charts have a real
trend instead of flat noise. ``_drift`` / ``_days`` are shared with
``ledger_lifestyle``, which carries the discretionary half.
"""

from __future__ import annotations

from datetime import date, timedelta

from backend.scripts.demo import rows
from backend.scripts.demo._timeline import Timeline, on_business_day, rng
from backend.scripts.demo.cast import (
    COFFEE,
    FUEL,
    GROCERS,
    PHARMACY,
    RESTAURANTS,
    RIDESHARE,
    SUBSCRIPTIONS,
    TAKEOUT,
)


def _drift(mi: int) -> float:
    return 1.0 + 0.0022 * mi


def _days(tl: Timeline, m: date):
    """Every day of month ``m`` that falls inside the timeline."""
    d = m.replace(day=1)
    while d.month == m.month:
        if tl.clamp(d) is not None:
            yield d
        d += timedelta(days=1)


def groceries(tl: Timeline, A: dict, C: dict) -> list[dict]:
    r = rng("groceries")
    out = []
    for mi, m in enumerate(tl.months):
        for d in _days(tl, m):
            # Weekly big shop on Saturday/Sunday, top-ups midweek.
            if d.weekday() in (5, 6):
                chance, low, high = 0.72, 78, 165
            elif d.weekday() in (1, 3):
                chance, low, high = 0.38, 18, 62
            else:
                continue
            if r.random() > chance:
                continue
            out.append(
                rows.expense(
                    d,
                    A["card"],
                    round(r.uniform(low, high) * _drift(mi), 2),
                    r.choice(GROCERS),
                    C["food"],
                )
            )
    return out


def dining(tl: Timeline, A: dict, C: dict) -> list[dict]:
    r = rng("dining")
    out = []
    for mi, m in enumerate(tl.months):
        for d in _days(tl, m):
            if d.weekday() < 5 and r.random() < 0.55:
                out.append(
                    rows.expense(
                        d,
                        A["card"],
                        round(r.uniform(4.2, 9.4) * _drift(mi), 2),
                        r.choice(COFFEE),
                        C["food"],
                    )
                )
            if r.random() < (0.34 if d.weekday() >= 4 else 0.13):
                out.append(
                    rows.expense(
                        d,
                        A["card"],
                        round(r.uniform(26, 118) * _drift(mi), 2),
                        r.choice(RESTAURANTS),
                        C["food"],
                    )
                )
            if r.random() < 0.11:
                out.append(
                    rows.expense(
                        d,
                        A["card"],
                        round(r.uniform(19, 48) * _drift(mi), 2),
                        r.choice(TAKEOUT),
                        C["food"],
                    )
                )
    return out


def transport(tl: Timeline, A: dict, C: dict) -> list[dict]:
    r = rng("transport")
    out = []
    for mi, m in enumerate(tl.months):
        for d in _days(tl, m):
            if r.random() < 0.10:
                out.append(
                    rows.expense(
                        d,
                        A["card"],
                        round(r.uniform(9, 34) * _drift(mi), 2),
                        r.choice(RIDESHARE),
                        C["transport"],
                    )
                )
            if d.weekday() == 2 and r.random() < 0.45:
                out.append(
                    rows.expense(
                        d,
                        A["checking"],
                        round(r.uniform(38, 74) * _drift(mi), 2),
                        r.choice(FUEL),
                        C["transport"],
                    )
                )
        d = tl.clamp(on_business_day(m.replace(day=4)))
        if d is not None:
            out.append(
                rows.expense(
                    d,
                    A["card"],
                    round(96.0 * _drift(mi), 2),
                    "CityLine Transit — Monthly Pass",
                    C["transport"],
                    tags=["recurring"],
                )
            )
        if m.month in (4, 10):
            d = tl.clamp(on_business_day(m.replace(day=17)))
            if d is not None:
                out.append(
                    rows.expense(
                        d,
                        A["card"],
                        round(r.uniform(180, 480), 2),
                        "Halden Auto Service",
                        C["transport"],
                        note="Scheduled service.",
                    )
                )
    return out


def subscriptions(tl: Timeline, A: dict, C: dict) -> list[dict]:
    """Fixed monthly charges — the set the Subscriptions view and the commitments are built on."""
    out = []
    for mi, m in enumerate(tl.months):
        for name, amount, day in SUBSCRIPTIONS:
            d = tl.clamp(m.replace(day=day))
            if d is None:
                continue
            # One price rise partway through, so a "your subscription went up" story exists.
            price = amount * (1.12 if (name == "Nimbus Streaming" and mi >= 17) else 1.0)
            out.append(
                rows.expense(
                    d,
                    A["card"],
                    round(price, 2),
                    name,
                    C["subscriptions"],
                    tags=["recurring"],
                )
            )
    return out


def health(tl: Timeline, A: dict, C: dict) -> list[dict]:
    r = rng("health")
    out = []
    for mi, m in enumerate(tl.months):
        d = tl.clamp(m.replace(day=7))
        if d is not None:
            out.append(
                rows.expense(
                    d,
                    A["card"],
                    round(52.0 * _drift(mi), 2),
                    "Summit Fitness",
                    C["health"],
                    tags=["recurring"],
                )
            )
        for d in _days(tl, m):
            if r.random() < 0.06:
                out.append(
                    rows.expense(
                        d,
                        A["card"],
                        round(r.uniform(12, 68) * _drift(mi), 2),
                        r.choice(PHARMACY),
                        C["health"],
                    )
                )
        if m.month in (2, 8):
            d = tl.clamp(on_business_day(m.replace(day=13)))
            if d is not None:
                out.append(
                    rows.expense(
                        d,
                        A["checking"],
                        round(r.uniform(95, 260), 2),
                        "Brightside Dental",
                        C["health"],
                    )
                )
    return out
