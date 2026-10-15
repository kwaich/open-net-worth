"""The demo book's inflows and its fixed obligations.

Payroll, the annual bonus, the Berlin freelance invoices, savings interest, the mortgage
(interest as an expense, principal as a transfer that actually shrinks the liability), the
student loan, utilities, and the monthly sweeps into savings and investments.
"""

from __future__ import annotations

from datetime import timedelta

from backend.scripts.demo import rows
from backend.scripts.demo._timeline import Timeline, last_business_day, on_business_day, rng
from backend.scripts.demo.cast import EMPLOYER, FREELANCE_CLIENT, UTILITIES

# Net pay per semi-monthly paycheck, by band of months since the timeline start.
# Two raises across the book, so the income chart has visible steps.
PAY_BANDS = [(0, 4610.0), (14, 4890.0), (26, 5180.0)]


def _net_pay(mi: int) -> float:
    amount = PAY_BANDS[0][1]
    for start, value in PAY_BANDS:
        if mi >= start:
            amount = value
    return amount


def salary(tl: Timeline, A: dict, C: dict) -> list[dict]:
    """Semi-monthly payroll: the 15th, and the last business day of the month."""
    r = rng("salary")
    out = []
    for mi, m in enumerate(tl.months):
        for pay_day in (on_business_day(m.replace(day=15)), last_business_day(m.year, m.month)):
            d = tl.clamp(pay_day)
            if d is None:
                continue
            amount = _net_pay(mi) + round(r.uniform(-18, 18), 2)
            out.append(
                rows.income(
                    d,
                    A["checking"],
                    amount,
                    f"{EMPLOYER} — Payroll",
                    C["salary"],
                    note="Net of tax, 401(k) and health premiums.",
                )
            )
    return out


def bonus(tl: Timeline, A: dict, C: dict) -> list[dict]:
    """Annual performance bonus, paid with the first March cheque."""
    out = []
    for mi, m in enumerate(tl.months):
        if m.month != 3:
            continue
        d = tl.clamp(on_business_day(m.replace(day=14)))
        if d is None:
            continue
        amount = 16400.0 + 900.0 * (mi // 12)
        out.append(
            rows.income(
                d,
                A["checking"],
                amount,
                f"{EMPLOYER} — Annual Bonus",
                C["salary"],
                note="Performance bonus, net.",
                tags=["bonus"],
            )
        )
    return out


def freelance(tl: Timeline, A: dict, C: dict) -> list[dict]:
    """Quarterly invoices from the Berlin client, paid in EUR into the Wavelet account."""
    out = []
    for mi, m in enumerate(tl.months):
        if m.month % 3 != 2:  # Feb / May / Aug / Nov
            continue
        d = tl.clamp(on_business_day(m.replace(day=8)))
        if d is None:
            continue
        amount = 2400.0 + 50.0 * (mi // 6)
        out.append(
            rows.income(
                d,
                A["eur"],
                amount,
                f"{FREELANCE_CLIENT} — Invoice",
                C["freelance"],
                note="Quarterly retainer, invoiced in EUR.",
                tags=["freelance"],
            )
        )
    return out


def freelance_repatriation(tl: Timeline, A: dict) -> list[dict]:
    """Most of each invoice comes home to USD; the rest stays to cover euro costs."""
    from backend.scripts.demo.cast import FX_RATES

    out = []
    for m in tl.months:
        if m.month % 3 != 2:
            continue
        d = tl.clamp(on_business_day(m.replace(day=12)))
        if d is None:
            continue
        eur = 1750.0
        out.append(
            rows.transfer(
                d,
                A["eur"],
                A["checking"],
                eur,
                "Wavelet — EUR to USD",
                to_amount=round(eur * FX_RATES["EUR"] - 6.0, 2),
                note="Bringing the invoice home; the fee comes off the USD leg.",
            )
        )
    return out


def euro_costs(tl: Timeline, A: dict, C: dict) -> list[dict]:
    """The euro-denominated costs the Berlin work actually incurs."""
    out = []
    for mi, m in enumerate(tl.months):
        d = tl.clamp(on_business_day(m.replace(day=4)))
        if d is not None:
            out.append(
                rows.expense(
                    d,
                    A["eur"],
                    round(189.0 + 1.5 * mi, 2),
                    "Werkraum Coworking",
                    C["home"],
                    tags=["freelance", "recurring"],
                )
            )
        if m.month % 3 == 2:
            d = tl.clamp(on_business_day(m.replace(day=21)))
            if d is not None:
                out.append(
                    rows.expense(
                        d,
                        A["eur"],
                        round(96.0, 2),
                        "Steuerberater Kranz",
                        C["tax"],
                        note="German bookkeeping for the freelance work.",
                    )
                )
    return out


def interest(tl: Timeline, A: dict, C: dict) -> list[dict]:
    """Monthly savings interest — small, but it is what makes an APY field mean something."""
    r = rng("interest")
    out = []
    for mi, m in enumerate(tl.months):
        d = tl.clamp(m.replace(day=1) + timedelta(days=2))
        if d is None:
            continue
        emergency = 21000.0 + 520.0 * mi
        out.append(
            rows.income(
                d,
                A["emergency"],
                round(emergency * 0.0435 / 12, 2),
                "Marlow Savings — Interest",
                C["investment_income"],
            )
        )
        out.append(
            rows.income(
                d,
                A["travelfund"],
                round((1800.0 + 190.0 * mi) * 0.041 / 12, 2),
                "Marlow Savings — Interest",
                C["investment_income"],
            )
        )
        if m.month % 3 == 0:
            out.append(
                rows.income(
                    d,
                    A["sgd"],
                    round(6800.0 * 0.029 / 4 + r.uniform(-1, 1), 2),
                    "Harbour Bank — Interest",
                    C["investment_income"],
                )
            )
    return out


# ── Debt: the mortgage and the student loan ───────────────────────────────────
# A payment is TWO facts. The interest half is money that left the household (an
# expense); the principal half is money that moved from an asset account onto the
# liability (a transfer). Booking the whole payment as one transfer would inflate net
# worth growth; booking it all as an expense would leave the mortgage frozen forever.

MORTGAGE_PAYMENT = 2650.0
MORTGAGE_START_PRINCIPAL = 408000.0
MORTGAGE_APR = 0.0525

STUDENT_PAYMENT = 385.0
STUDENT_START_PRINCIPAL = 18400.0
STUDENT_APR = 0.041


def _amortize(
    tl: Timeline,
    A: dict,
    C: dict,
    *,
    account: str,
    label: str,
    payment: float,
    principal: float,
    apr: float,
    day: int,
) -> list[dict]:
    out = []
    for m in tl.months:
        d = tl.clamp(on_business_day(m.replace(day=day)))
        if d is None:
            break
        interest_part = round(principal * apr / 12, 2)
        principal_part = round(min(payment - interest_part, principal), 2)
        if principal_part <= 0:
            break
        out.append(
            rows.expense(
                d,
                A["checking"],
                interest_part,
                f"{label} — Interest",
                C["home"] if account == "mortgage" else C["other"],
                note="Interest portion of the monthly payment.",
            )
        )
        out.append(
            rows.transfer(
                d,
                A["checking"],
                A[account],
                principal_part,
                f"{label} — Principal",
                note="Principal portion — reduces the loan balance.",
            )
        )
        principal = round(principal - principal_part, 2)
        if principal <= 0:
            break
    return out


def mortgage(tl: Timeline, A: dict, C: dict) -> list[dict]:
    return _amortize(
        tl,
        A,
        C,
        account="mortgage",
        label="Northbridge Mortgage",
        payment=MORTGAGE_PAYMENT,
        principal=MORTGAGE_START_PRINCIPAL,
        apr=MORTGAGE_APR,
        day=3,
    )


def student_loan(tl: Timeline, A: dict, C: dict) -> list[dict]:
    return _amortize(
        tl,
        A,
        C,
        account="studentloan",
        label="Fed Direct Student Loan",
        payment=STUDENT_PAYMENT,
        principal=STUDENT_START_PRINCIPAL,
        apr=STUDENT_APR,
        day=20,
    )


def utilities(tl: Timeline, A: dict, C: dict) -> list[dict]:
    """Household bills out of the joint account — seasonal on energy, flat on the rest."""
    r = rng("utilities")
    out = []
    for mi, m in enumerate(tl.months):
        drift = 1.0 + 0.0022 * mi
        for name, base, swing, day in UTILITIES:
            d = tl.clamp(on_business_day(m.replace(day=day)))
            if d is None:
                continue
            # Northern-hemisphere shape: heating in Jan, cooling in Aug.
            season = abs(m.month - 7) / 6.0
            amount = base * drift + swing * season + r.uniform(-4, 4)
            out.append(
                rows.expense(d, A["joint"], max(amount, 8.0), name, C["home"], tags=["bills"])
            )
        d = tl.clamp(on_business_day(m.replace(day=5)))
        if d is not None:
            out.append(
                rows.expense(
                    d,
                    A["joint"],
                    round(268.0 * drift, 2),
                    "Pearl Street HOA",
                    C["home"],
                    tags=["bills"],
                )
            )
        d = tl.clamp(on_business_day(m.replace(day=22)))
        if d is not None and m.month % 6 == 4:
            out.append(
                rows.expense(
                    d,
                    A["joint"],
                    round(742.0 * drift, 2),
                    "Anchor Home Insurance",
                    C["home"],
                    note="Semi-annual premium.",
                )
            )
    return out


def household_funding(tl: Timeline, A: dict) -> list[dict]:
    """Alex's share into the joint account, on payday."""
    out = []
    for mi, m in enumerate(tl.months):
        d = tl.clamp(on_business_day(m.replace(day=16)))
        if d is None:
            continue
        out.append(
            rows.transfer(
                d,
                A["checking"],
                A["joint"],
                round(720.0 + 5.0 * mi, 2),
                "Transfer to Joint Household",
            )
        )
    return out


def sweeps(tl: Timeline, A: dict, trades: list[dict]) -> list[dict]:
    """The automatic monthly savings/investing sweeps — the habit the book is built on.

    The savings legs are flat amounts; the INVESTING legs are derived from that month's
    actual purchases plus a small residue, because a brokerage sweep that does not match
    what was bought leaves the settlement-cash balance drifting away from zero in one
    direction for thirty months. Deriving it is what keeps every investment account's cash
    at the small, plausible residue a real one carries.
    """
    savings = [
        ("emergency", 500.0, 6.0, "Auto-save — Emergency Fund"),
        ("travelfund", 300.0, 0.0, "Auto-save — Travel Fund"),
    ]
    funded: dict[tuple[str, str], float] = {}
    for order in trades:
        if order["account"] not in ("brokerage", "roth", "crypto"):
            continue
        key = (order["account"], order["date"].strftime("%Y-%m"))
        funded[key] = funded.get(key, 0.0) + order["gross"]

    labels = {
        "brokerage": "Transfer to Taxable Brokerage",
        "roth": "Transfer to Roth IRA",
        "crypto": "Transfer to Lattice Markets",
    }
    out = []
    for mi, m in enumerate(tl.months):
        d = tl.clamp(on_business_day(m.replace(day=2)))
        if d is None:
            continue
        for key, base, growth, label in savings:
            out.append(rows.transfer(d, A["checking"], A[key], round(base + growth * mi, 2), label))
        for account, label in labels.items():
            amount = funded.get((account, m.strftime("%Y-%m")), 0.0)
            if amount <= 0:
                continue
            # Round the sweep up to the nearest $10 — nobody transfers $1,412.37.
            out.append(
                rows.transfer(
                    d, A["checking"], A[account], float(int(amount / 10) * 10 + 10), label
                )
            )
    return out


def fx_top_ups(tl: Timeline, A: dict) -> list[dict]:
    """Occasional USD → EUR top-ups. Two legs, two currencies, one move."""
    from backend.scripts.demo.cast import FX_RATES

    out = []
    for mi, m in enumerate(tl.months):
        if mi % 9 != 4:
            continue
        d = tl.clamp(on_business_day(m.replace(day=24)))
        if d is None:
            continue
        eur = 600.0
        out.append(
            rows.transfer(
                d,
                A["checking"],
                A["eur"],
                round(eur * FX_RATES["EUR"] + 4.5, 2),
                "Wavelet — USD to EUR top-up",
                to_amount=eur,
                note="Includes the transfer fee on the USD leg.",
            )
        )
    return out
