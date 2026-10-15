"""The demo book's DISCRETIONARY spending — shopping, fun, pets, gifts, trips, tax, cash.

The other half of ``ledger_spending`` (which carries the essentials). Split for file size
only; the two share ``_drift``/``_days`` and the same card-first routing convention.
"""

from __future__ import annotations

from backend.scripts.demo import rows
from backend.scripts.demo._timeline import Timeline, add_months, on_business_day, rng
from backend.scripts.demo.cast import ENTERTAINMENT, HOBBY, SHOPS
from backend.scripts.demo.ledger_spending import _days, _drift


def shopping(tl: Timeline, A: dict, C: dict) -> list[dict]:
    r = rng("shopping")
    out = []
    for mi, m in enumerate(tl.months):
        for d in _days(tl, m):
            if r.random() < 0.19:
                out.append(
                    rows.expense(
                        d,
                        A["card"],
                        round(r.uniform(14, 165) * _drift(mi), 2),
                        r.choice(SHOPS),
                        C["shopping"],
                    )
                )
    return out


def entertainment(tl: Timeline, A: dict, C: dict) -> list[dict]:
    r = rng("entertainment")
    out = []
    for mi, m in enumerate(tl.months):
        for d in _days(tl, m):
            if d.weekday() >= 4 and r.random() < 0.18:
                out.append(
                    rows.expense(
                        d,
                        A["card"],
                        round(r.uniform(16, 96) * _drift(mi), 2),
                        r.choice(ENTERTAINMENT),
                        C["entertainment"],
                    )
                )
    return out


def pets(tl: Timeline, A: dict, C: dict) -> list[dict]:
    r = rng("pets")
    out = []
    for mi, m in enumerate(tl.months):
        d = tl.clamp(m.replace(day=11))
        if d is not None:
            out.append(
                rows.expense(
                    d,
                    A["card"],
                    round(r.uniform(48, 82) * _drift(mi), 2),
                    "Kibble Corner",
                    C["pets"],
                )
            )
        if m.month % 6 == 1:
            d = tl.clamp(on_business_day(m.replace(day=19)))
            if d is not None:
                out.append(
                    rows.expense(
                        d,
                        A["card"],
                        round(r.uniform(140, 320), 2),
                        "Paws & Claws Vet",
                        C["pets"],
                        note="Biannual check-up for Nori.",
                    )
                )
    return out


def hobby(tl: Timeline, A: dict, C: dict) -> list[dict]:
    r = rng("hobby")
    out = []
    for mi, m in enumerate(tl.months):
        for d in _days(tl, m):
            if r.random() < 0.055:
                out.append(
                    rows.expense(
                        d,
                        A["card"],
                        round(r.uniform(22, 210) * _drift(mi), 2),
                        r.choice(HOBBY),
                        C["hobby"],
                    )
                )
    return out


def giving(tl: Timeline, A: dict, C: dict) -> list[dict]:
    out = []
    for mi, m in enumerate(tl.months):
        d = tl.clamp(on_business_day(m.replace(day=6)))
        if d is None:
            continue
        out.append(
            rows.expense(
                d,
                A["checking"],
                round(120.0 + 2.0 * mi, 2),
                "Rivermouth Food Bank",
                C["giving"],
                tags=["recurring"],
            )
        )
    return out


def gifts(tl: Timeline, A: dict, C: dict) -> list[dict]:
    """December, plus two birthdays — the December spike every real book has."""
    r = rng("gifts")
    out = []
    for mi, m in enumerate(tl.months):
        count = 7 if m.month == 12 else (2 if m.month in (5, 9) else 0)
        for i in range(count):
            d = tl.clamp(m.replace(day=min(3 + i * 3, 27)))
            if d is None:
                continue
            out.append(
                rows.expense(
                    d,
                    A["card"],
                    round(r.uniform(28, 190) * _drift(mi), 2),
                    r.choice(SHOPS + ["Paper Lantern Gifts"]),
                    C["family"],
                    tags=["gifts"],
                )
            )
    return out


def travel(tl: Timeline, A: dict, C: dict) -> list[dict]:
    """Two trips a year: a long summer one, a short winter one."""
    r = rng("travel")
    trips = {
        7: ("Lisbon", 5, 520, 1450),
        2: ("Vancouver", 3, 280, 720),
    }
    out = []
    for mi, m in enumerate(tl.months):
        trip = trips.get(m.month)
        if trip is None:
            continue
        place, legs, low, high = trip
        # Flights are booked two months out — which is also what keeps one trip from
        # landing an entire holiday on a single card statement.
        d = tl.clamp(on_business_day(add_months(m, -2).replace(day=6)))
        if d is not None:
            out.append(
                rows.expense(
                    d,
                    A["card"],
                    round(r.uniform(low * 1.4, high * 1.4), 2),
                    "Skyward Air",
                    C["travel"],
                    note=f"{place} trip — flights.",
                    tags=["travel", place.lower()],
                )
            )
        for i in range(legs):
            d = tl.clamp(m.replace(day=min(12 + i * 2, 27)))
            if d is None:
                continue
            out.append(
                rows.expense(
                    d,
                    A["card"],
                    round(r.uniform(low, high), 2),
                    r.choice(["Casa Aurora Stays", "Harbourview Hotel", "Nomad Rentals"]),
                    C["travel"],
                    note=f"{place} trip.",
                    tags=["travel", place.lower()],
                )
            )
        d = tl.clamp(m.replace(day=min(14, 28)))
        if d is not None:
            out.append(
                rows.transfer(
                    d,
                    A["travelfund"],
                    A["checking"],
                    round(r.uniform(700, 1300), 2),
                    "Travel Fund — trip drawdown",
                    note=f"Paying for the {place} trip out of the sinking fund.",
                )
            )
    return out


def taxes(tl: Timeline, A: dict, C: dict) -> list[dict]:
    """Quarterly estimated tax on the freelance income."""
    out = []
    for mi, m in enumerate(tl.months):
        if m.month not in (1, 4, 6, 9):
            continue
        d = tl.clamp(on_business_day(m.replace(day=15)))
        if d is None:
            continue
        out.append(
            rows.expense(
                d,
                A["checking"],
                round(520.0 + 7.0 * mi, 2),
                "IRS — Estimated Tax",
                C["tax"],
                note="Quarterly estimate on the freelance income.",
            )
        )
    return out


def cash(tl: Timeline, A: dict, C: dict) -> list[dict]:
    """ATM withdrawals and the small cash spending they fund."""
    r = rng("cash")
    out = []
    for mi, m in enumerate(tl.months):
        d = tl.clamp(on_business_day(m.replace(day=9)))
        if d is None:
            continue
        out.append(
            rows.transfer(d, A["checking"], A["wallet"], 120.0, "ATM withdrawal — Northbridge")
        )
        for i in range(r.randint(3, 6)):
            dd = tl.clamp(m.replace(day=min(10 + i * 3, 28)))
            if dd is None:
                continue
            out.append(
                rows.expense(
                    dd,
                    A["wallet"],
                    round(r.uniform(6, 34), 2),
                    r.choice(["Farmers Market", "Corner Newsstand", "Laundry", "Barber"]),
                    r.choice([C["food"], C["lifestyle"], C["other"]]),
                )
            )
    return out


def split_runs(tl: Timeline, A: dict, C: dict) -> list[dict]:
    """The big-box run that is genuinely three categories in one charge."""
    r = rng("splits")
    out = []
    for mi, m in enumerate(tl.months):
        if mi % 2:
            continue
        d = tl.clamp(m.replace(day=min(21, 28)))
        if d is None:
            continue
        food = round(r.uniform(52, 118), 2)
        home = round(r.uniform(18, 64), 2)
        shop = round(r.uniform(14, 72), 2)
        out.append(
            rows.split(
                d,
                A["card"],
                "Everyday Depot",
                [
                    {"category": C["food"], "amount": food, "note": "Pantry"},
                    {"category": C["home"], "amount": home, "note": "Cleaning + bulbs"},
                    {"category": C["shopping"], "amount": shop, "note": "Everything else"},
                ],
            )
        )
    return out
