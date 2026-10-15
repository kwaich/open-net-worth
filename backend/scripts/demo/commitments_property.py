"""The demo's flagship commitment: an off-plan apartment bought on a staged payment schedule.

Modelled on the shape a real one takes. A commitment that matters is not "a total and a date"
— it is a dated LADDER of payments, funded by a dozen different things arriving at a dozen
different times, with a checklist of what has to happen to make each one land. The rest of the
demo's commitments are deliberately simple; this is the one that shows what the feature is for.

What it exercises that a simple commitment does not:

* **Funding sources are dated and specific.** Monthly set-asides, each with its own
  ``available_date`` and the account it lands in — not one round "savings" figure.
* **Asset sales name the position and the quantity.** ``ref_position_id`` + ``sell_quantity``
  point at holdings this book actually owns, so the plan can be checked against the portfolio
  rather than asserted.
* **Actions link back to their funding source** via ``funding_id``, which is why the funding
  ids are assigned HERE rather than left to the store to mint: an action has to be able to
  name the money it depends on.
* **Every status is represented** — paid instalments behind, upcoming ahead; actions done,
  pending and missed — because a screen that only renders one state proves nothing.
"""

from __future__ import annotations

from datetime import date

from backend.scripts.demo._timeline import Timeline, add_months

# Instalment ladder: (months from today, label, amount). Negative = already paid.
_LADDER: list[tuple[int, str, float]] = [
    (-14, "Booking deposit", 5000.0),
    (-12, "1st — on signing", 24000.0),
    (-9, "2nd — foundation", 24000.0),
    (-5, "3rd — structure", 24000.0),
    (-1, "4th — topping out", 24000.0),
    (3, "5th — facade", 24000.0),
    (7, "6th — fit-out", 24000.0),
    (11, "7th — handover", 32000.0),
    (13, "Registration & fees", 9500.0),
]

# Which holdings get trimmed to fund which instalment: (months out, symbol, units, note).
_SALES: list[tuple[int, str, float, str]] = [
    (2, "VOO", 18.0, "Long-term lots only — short-term would be taxed as income."),
    (6, "NVDA", 12.0, "Trimming the position that ran up the most."),
    (10, "AAPL", 20.0, "Last resort before drawing on the loan."),
]


def property_purchase(tl: Timeline, accounts: dict, positions: dict) -> dict:
    """The staged apartment purchase, as a ``CommitmentsStore.create`` payload."""
    today = tl.today
    funding: list[dict] = []
    actions: list[dict] = []

    # ── Monthly set-asides: the backbone of the plan ──────────────────────────
    # One per month, each dated to the day the money is actually put aside. A single
    # "save $2,000/mo" source would render as one row and project as a straight line; the real
    # thing is a column of dated commitments you can tick off, and miss.
    for i in range(-6, 14):
        when = add_months(today.replace(day=28), i)
        fid = f"fs-setaside-{when:%Y%m}"
        funding.append(
            {
                "id": fid,
                "type": "income",
                "label": f"Set-aside {when:%b %Y}",
                "amount": 2100.0,
                "currency": "USD",
                "available_date": when.isoformat(),
                "ref_account_id": accounts["emergency"],
            }
        )
        # Only the near-term ones carry a checklist entry — a to-do list two years long is noise.
        if -2 <= i <= 3:
            actions.append(
                {
                    "label": f"Move {when:%b} set-aside to the purchase fund",
                    "category": "Savings",
                    "due_date": when.isoformat(),
                    "amount": 2100.0,
                    "currency": "USD",
                    "status": "done" if i < 0 else "pending",
                    "funding_id": fid,
                }
            )

    # ── Asset sales: named positions, named quantities ────────────────────────
    for months, symbol, units, note in _SALES:
        when = add_months(today.replace(day=15), months)
        position_id = positions.get(("brokerage", symbol))
        fid = f"fs-sale-{symbol.lower()}"
        funding.append(
            {
                "id": fid,
                "type": "asset_sale",
                "label": f"Sell {units:g} {symbol} for the {months}-month instalment",
                "amount": round(units * _INDICATIVE[symbol], 2),
                "currency": "USD",
                "available_date": when.isoformat(),
                "ref_position_id": position_id,
                "sell_quantity": units,
                "note": note,
            }
        )
        actions.append(
            {
                "label": f"Place the {symbol} sell order",
                "category": "Stock Sale",
                "due_date": when.isoformat(),
                "start_by": add_months(when, -1).isoformat(),
                "amount": round(units * _INDICATIVE[symbol], 2),
                "currency": "USD",
                "status": "pending",
                "counterparty": "Meridian Wealth",
                "funding_id": fid,
            }
        )

    # ── Windfalls and the loan of last resort ─────────────────────────────────
    bonus_at = add_months(today.replace(day=14), (3 - today.month) % 12 or 12)
    funding.append(
        {
            "id": "fs-bonus",
            "type": "windfall",
            "label": "Annual bonus (net)",
            "amount": 16400.0,
            "currency": "USD",
            "available_date": bonus_at.isoformat(),
        }
    )
    funding.append(
        {
            "id": "fs-refund",
            "type": "windfall",
            "label": "Tax refund",
            "amount": 2800.0,
            "currency": "USD",
            "available_date": add_months(today.replace(day=20), 5).isoformat(),
        }
    )
    funding.append(
        {
            "id": "fs-loan",
            "type": "loan",
            "label": "Northbridge personal loan (24-mo) — only if the sales fall short",
            "amount": 20000.0,
            "currency": "USD",
            "available_date": add_months(today.replace(day=10), 10).isoformat(),
            "disabled": True,
            "note": "Held in reserve. Enabling this is the decision, not the fallback.",
        }
    )

    actions.extend(_checklist(today, bonus_at))

    return {
        "name": "Lakeside Residences — 2 bed",
        "kind": "purchase",
        "currency": "USD",
        "note": (
            "Off-plan, handover in about a year. Paid in stages against construction "
            "milestones — the schedule is the developer's, not ours."
        ),
        "installments": [
            {
                "label": label,
                "due_date": add_months(today.replace(day=20), months).isoformat(),
                "amount": amount,
                "status": "paid" if months < 0 else ("due" if months <= 3 else "upcoming"),
            }
            for months, label, amount in _LADDER
        ],
        "funding_plan": funding,
        "actions": actions,
    }


# Indicative marks, only used to size a sale's expected proceeds in the PLAN. The plan must not
# drift with the market (services/commitments.py stores a frozen amount per source for exactly
# this reason), so these are deliberately static rather than read from live prices.
_INDICATIVE = {"VOO": 700.0, "NVDA": 215.0, "AAPL": 310.0}


def _checklist(today: date, bonus_at: date) -> list[dict]:
    """The non-payment work: approvals, paperwork, transfers, reviews."""
    return [
        {
            "label": "Lawyer to review the sale-and-purchase agreement",
            "category": "Pre-arrangement",
            "due_date": add_months(today, -13).isoformat(),
            "status": "done",
            "counterparty": "Hale & Fry",
        },
        {
            "label": "Confirm the developer's escrow account",
            "category": "Pre-arrangement",
            "due_date": add_months(today, -12).isoformat(),
            "status": "done",
            "counterparty": "Lakeside Developments",
        },
        {
            "label": "Site visit before the structure payment",
            "category": "Review",
            "due_date": add_months(today, -6).isoformat(),
            "status": "missed",
            "note": "Went ahead on the surveyor's report instead.",
        },
        {
            "label": "Snagging inspection before handover",
            "category": "Review",
            "due_date": add_months(today, 10).isoformat(),
            "start_by": add_months(today, 9).isoformat(),
            "status": "pending",
            "counterparty": "Ridge & Oak Studio",
        },
        {
            "label": "Wire the facade instalment",
            "category": "Apt Payment",
            "due_date": add_months(today.replace(day=20), 3).isoformat(),
            "amount": 24000.0,
            "currency": "USD",
            "status": "pending",
            "counterparty": "Lakeside Developments",
        },
        {
            "label": "Move the bonus into the purchase fund",
            "category": "Cash Transfer",
            "due_date": bonus_at.isoformat(),
            "amount": 16400.0,
            "currency": "USD",
            "status": "pending",
            "funding_id": "fs-bonus",
        },
        {
            "label": "Decide on the loan — or confirm it is not needed",
            "category": "Loan Drawdown",
            "due_date": add_months(today, 9).isoformat(),
            "start_by": add_months(today, 8).isoformat(),
            "status": "pending",
            "counterparty": "Northbridge Bank",
            "funding_id": "fs-loan",
            "note": "Decide once the AAPL sale has settled and the gap is known.",
        },
        {
            "label": "Register the title and pay the stamp duty",
            "category": "Pre-arrangement",
            "due_date": add_months(today, 13).isoformat(),
            "amount": 9500.0,
            "currency": "USD",
            "status": "pending",
        },
    ]
