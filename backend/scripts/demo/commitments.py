"""The demo's dated obligations — what the Plan tab projects forward.

Split out of ``planning`` for file size: this is pure data, and there is a lot of it.

The set is chosen to exercise the surface, not just to fill it. Between them these cover
every ``kind`` (purchase / loan_repayment / auto_pay / goal), every funding-source type
(cash / income / asset_sale / loan / windfall), installments in all three states, actions in
all three states, an amortizing loan with real terms, a commitment already completed, and one
funded by selling a holding the demo actually owns. A screen that only ever renders
"upcoming, unfunded" tells you nothing about how the feature behaves.
"""

from __future__ import annotations

from backend.scripts.demo._timeline import Timeline, add_months


def _monthly(label: str, amount: float, currency: str = "USD") -> dict:
    return {
        "type": "income",
        "label": label,
        "amount": amount,
        "currency": currency,
        "cadence": "monthly",
    }


def _loan_installments(tl: Timeline, payment: float, back: int, ahead: int) -> list[dict]:
    """A payment ladder straddling today: the ones behind are paid, the ones ahead are not.

    A loan rendered with no installments is the flattest thing on the screen — it shows a
    balance and nothing about rhythm or progress, which is most of what a repayment plan IS.
    """
    rows = []
    for i in range(-back, ahead + 1):
        due = add_months(tl.today.replace(day=3), i)
        if i < 0:
            status = "paid"
        elif i == 0:
            status = "due"
        else:
            status = "upcoming"
        rows.append(
            {
                "label": due.strftime("%b %Y"),
                "due_date": due.isoformat(),
                "amount": payment,
                "status": status,
            }
        )
    return rows


def COMMITMENTS(tl: Timeline, accounts: dict, positions: dict) -> list[dict]:
    """Every commitment the demo book carries, as ``CommitmentsStore.create`` payloads.

    ``positions`` lets a funding source name a holding this book actually owns, so an
    asset-sale plan can be checked against the portfolio instead of merely asserted.
    """
    from backend.scripts.demo.commitments_property import property_purchase

    return [
        property_purchase(tl, accounts, positions),
        # ── Loans: the two the ledger actually amortizes ──────────────────────
        {
            "name": "Mortgage",
            "kind": "loan_repayment",
            "currency": "USD",
            "linked_account_id": accounts["mortgage"],
            "note": "Northbridge, 30-year fixed at 5.25%. Overpaying is not worth it below 6%.",
            "loan": {"principal": 425000.0, "apr": 5.25, "term_months": 360, "payment": 2650.0},
            "installments": _loan_installments(tl, 2650.0, back=4, ahead=6),
            "funding_plan": [_monthly("Payroll", 2650.0)],
            "actions": [
                {
                    "label": "Check whether refinancing beats 5.25% yet",
                    "due_date": add_months(tl.today, 4).isoformat(),
                    "status": "pending",
                    "category": "review",
                }
            ],
        },
        {
            "name": "Student loan",
            "kind": "loan_repayment",
            "currency": "USD",
            "linked_account_id": accounts["studentloan"],
            "note": "Two years left at the current rate.",
            "loan": {"principal": 41000.0, "apr": 4.1, "term_months": 120, "payment": 385.0},
            "installments": _loan_installments(tl, 385.0, back=3, ahead=5),
            "funding_plan": [_monthly("Payroll", 385.0)],
        },
        # ── A purchase mid-flight: deposit paid, the rest ahead ───────────────
        {
            "name": "Kitchen renovation",
            "kind": "purchase",
            "currency": "USD",
            "note": "Quotes in from two contractors. Design signed off; deposit is down.",
            "installments": [
                {
                    "label": "Design deposit",
                    "due_date": add_months(tl.today, -1).isoformat(),
                    "amount": 4500.0,
                    "status": "paid",
                },
                {
                    "label": "Materials",
                    "due_date": add_months(tl.today, 3).isoformat(),
                    "amount": 12500.0,
                    "status": "upcoming",
                },
                {
                    "label": "Final payment",
                    "due_date": add_months(tl.today, 6).isoformat(),
                    "amount": 9000.0,
                    "status": "upcoming",
                },
            ],
            "funding_plan": [
                {
                    "type": "cash",
                    "label": "Emergency Fund (above the six-month floor)",
                    "amount": 12000.0,
                    "currency": "USD",
                    "ref_account_id": accounts["emergency"],
                },
                {
                    "type": "income",
                    "label": "Next annual bonus",
                    "amount": 14000.0,
                    "currency": "USD",
                    "available_date": add_months(tl.today, 7).isoformat(),
                },
            ],
            "actions": [
                {
                    "label": "Confirm the contractor's insurance",
                    "due_date": add_months(tl.today, 1).isoformat(),
                    "status": "pending",
                    "counterparty": "Ridge & Oak Studio",
                },
                {
                    "label": "Sign off the final drawings",
                    "due_date": add_months(tl.today, -1).isoformat(),
                    "status": "done",
                    "counterparty": "Ridge & Oak Studio",
                },
                {
                    "label": "Get the third quote",
                    "due_date": add_months(tl.today, -2).isoformat(),
                    "status": "missed",
                    "note": "Never came back to us; went with two.",
                },
            ],
        },
        # ── A goal funded partly by SELLING something the book owns ───────────
        {
            "name": "Japan trip",
            "kind": "goal",
            "currency": "USD",
            "note": "Two weeks next spring, two people.",
            "installments": [
                {
                    "label": "Flights",
                    "due_date": add_months(tl.today, 3).isoformat(),
                    "amount": 3100.0,
                    "status": "upcoming",
                },
                {
                    "label": "Accommodation",
                    "due_date": add_months(tl.today, 5).isoformat(),
                    "amount": 2800.0,
                    "status": "upcoming",
                },
                {
                    "label": "Rail passes",
                    "due_date": add_months(tl.today, 5).isoformat(),
                    "amount": 620.0,
                    "status": "upcoming",
                },
            ],
            "funding_plan": [
                {
                    "type": "cash",
                    "label": "Travel Fund",
                    "amount": 5900.0,
                    "currency": "USD",
                    "ref_account_id": accounts["travelfund"],
                },
                {
                    "type": "windfall",
                    "label": "Cashback + airline points rebate",
                    "amount": 620.0,
                    "currency": "USD",
                    "available_date": add_months(tl.today, 2).isoformat(),
                },
            ],
            "actions": [
                {
                    "label": "Book flights while the fare sale is on",
                    "due_date": add_months(tl.today, 3).isoformat(),
                    "start_by": add_months(tl.today, 2).isoformat(),
                    "status": "pending",
                    "amount": 3100.0,
                    "currency": "USD",
                }
            ],
        },
        # ── Recurring auto-pays: the quiet ones that still hit the plan ───────
        {
            "name": "Home insurance",
            "kind": "auto_pay",
            "currency": "USD",
            "linked_account_id": accounts["joint"],
            "note": "Anchor Home — renews twice a year on auto-debit.",
            "installments": [
                {
                    "label": "Spring premium",
                    "due_date": add_months(tl.today, -2).isoformat(),
                    "amount": 742.0,
                    "status": "paid",
                },
                {
                    "label": "Autumn premium",
                    "due_date": add_months(tl.today, 4).isoformat(),
                    "amount": 780.0,
                    "status": "upcoming",
                },
            ],
            "funding_plan": [_monthly("Household transfer", 130.0)],
        },
        {
            "name": "Property tax",
            "kind": "auto_pay",
            "currency": "USD",
            "linked_account_id": accounts["joint"],
            "note": "Billed semi-annually by the county.",
            "installments": [
                {
                    "label": "First half",
                    "due_date": add_months(tl.today, 1).isoformat(),
                    "amount": 3180.0,
                    "status": "due",
                },
                {
                    "label": "Second half",
                    "due_date": add_months(tl.today, 7).isoformat(),
                    "amount": 3180.0,
                    "status": "upcoming",
                },
            ],
            "funding_plan": [_monthly("Set aside monthly", 530.0)],
        },
        {
            "name": "Car insurance & service",
            "kind": "auto_pay",
            "currency": "USD",
            "linked_account_id": accounts["checking"],
            "installments": [
                {
                    "label": "Annual premium",
                    "due_date": add_months(tl.today, 2).isoformat(),
                    "amount": 1240.0,
                    "status": "upcoming",
                },
                {
                    "label": "Major service",
                    "due_date": add_months(tl.today, 5).isoformat(),
                    "amount": 480.0,
                    "status": "upcoming",
                },
            ],
        },
        # ── A stretch purchase leaning on an asset sale + a loan ──────────────
        {
            "name": "Replace the roof",
            "kind": "purchase",
            "currency": "USD",
            "note": "Surveyor says three to five years left. Getting ahead of it.",
            "installments": [
                {
                    "label": "Scaffolding + strip",
                    "due_date": add_months(tl.today, 11).isoformat(),
                    "amount": 8000.0,
                    "status": "upcoming",
                },
                {
                    "label": "Materials + labour",
                    "due_date": add_months(tl.today, 12).isoformat(),
                    "amount": 21000.0,
                    "status": "upcoming",
                },
            ],
            "funding_plan": [
                {
                    "type": "asset_sale",
                    "label": "Trim the taxable brokerage",
                    "amount": 15000.0,
                    "currency": "USD",
                    "available_date": add_months(tl.today, 10).isoformat(),
                    "note": "Long-term lots only — short-term would be taxed as income.",
                },
                {
                    "type": "loan",
                    "label": "Home-improvement loan if the market is down",
                    "amount": 14000.0,
                    "currency": "USD",
                    "available_date": add_months(tl.today, 11).isoformat(),
                    "disabled": True,
                    "note": "Fallback only — disabled while the brokerage covers it.",
                },
            ],
            "actions": [
                {
                    "label": "Second surveyor opinion",
                    "due_date": add_months(tl.today, 6).isoformat(),
                    "status": "pending",
                },
            ],
        },
        # ── Something already finished, so the screen has history ─────────────
        {
            "name": "Replace the car",
            "kind": "purchase",
            "currency": "USD",
            "status": "completed",
            "note": "Picked up the estate in July. Paid outright, no financing.",
            "installments": [
                {
                    "label": "Deposit",
                    "due_date": add_months(tl.today, -4).isoformat(),
                    "amount": 3000.0,
                    "status": "paid",
                },
                {
                    "label": "Balance on collection",
                    "due_date": add_months(tl.today, -2).isoformat(),
                    "amount": 16000.0,
                    "status": "paid",
                },
            ],
            "funding_plan": [
                {
                    "type": "cash",
                    "label": "Car fund",
                    "amount": 19000.0,
                    "currency": "USD",
                    "ref_account_id": accounts["emergency"],
                }
            ],
            "actions": [
                {
                    "label": "Cancel the old policy",
                    "due_date": add_months(tl.today, -2).isoformat(),
                    "status": "done",
                }
            ],
        },
        # ── A euro-denominated one, so the screen is not single-currency ──────
        {
            "name": "Berlin coworking renewal",
            "kind": "auto_pay",
            "currency": "EUR",
            "linked_account_id": accounts["eur"],
            "note": "Werkraum — annual desk, billed in euros.",
            "installments": [
                {
                    "label": "Annual desk",
                    "due_date": add_months(tl.today, 3).isoformat(),
                    "amount": 2280.0,
                    "status": "upcoming",
                }
            ],
            "funding_plan": [
                {
                    "type": "income",
                    "label": "Blaubeere retainer",
                    "amount": 2280.0,
                    "currency": "EUR",
                    "cadence": "quarterly",
                }
            ],
        },
    ]
