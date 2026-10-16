"""The bills the Dashboard's "Next 30 days" lists beside commitments.

Commitments alone are not the next 30 days: rent, subscriptions and credit-card statements leave
the household too, and a card that showed the salary coming in but not the rent going out read
as the month being cheaper than it is. Two more sources, each read from the ONE place that
already knows it:

  * **Recurring bills** — the active money-OUT recurring streams the cash-flow projection walks
    (``recurring_baseline.upcoming_bills``). A bill that a commitment already lists (the same
    amount within a few days — a loan's auto-debit tracked as a commitment AND detected as a
    stream) is left out, so it is never counted twice.
  * **Card bills** — each live credit card whose last statement still owes money and falls due
    inside the window, at ``card_statements.statement_summary``'s ``remaining_amount``, the one
    "still owed" figure. A charge made from today on lands on a LATER statement, so a
    subscription billed to a card and that card's statement never overlap here.

The Dashboard's "Needs attention" reads the same card statements through ``cards_due_soon``:
only a statement still owing money that is due within ``CARD_DUE_SOON_DAYS`` or already past
its printed due date. Owing money on a card is not a problem; a bill about to fall due is.
"""

from __future__ import annotations

from datetime import date

from backend.db.account_store import AccountStore
from backend.db.transaction_store import TransactionStore
from backend.services import ledger as svc
from backend.services import recurring, recurring_baseline
from backend.services.card_statements import statement_summary

# A commitment and a stream are the same payment when their amounts agree and their dates are
# this close — a bank shifts an auto-debit off a weekend.
SAME_PAYMENT_DAYS = 3

# A card statement earns a Needs attention flag this many days before it is due.
CARD_DUE_SOON_DAYS = 7


def recurring_bills(
    days: int, base: str, rates: dict, today: date, commitment_events: list[dict]
) -> list[dict]:
    """Recurring outflows due after today through ``today + days``, minus those a commitment
    event already lists."""
    tracked = [
        (date.fromisoformat(e["date"]), float(e["amount_base"]))
        for e in commitment_events
        if e.get("kind") != "funding" and e.get("amount_base")
    ]
    return [
        bill
        for bill in recurring_baseline.upcoming_bills(days, base, rates, today=today)
        if not _tracked(bill, tracked)
    ]


def _tracked(bill: dict, tracked: list[tuple[date, float]]) -> bool:
    when = date.fromisoformat(bill["date"])
    return any(
        abs((when - d).days) <= SAME_PAYMENT_DAYS
        and recurring._matches_a_known_amount(float(bill["amount_base"]), [amount])
        for d, amount in tracked
    )


def card_bills(
    days: int, base: str, rates: dict, today: date, statements: list[dict] | None = None
) -> list[dict]:
    """Each live card's unpaid statement due today through ``today + days``, soonest first."""
    if statements is None:
        statements = unpaid_statements(base, rates, today)
    return [b for b in statements if 0 <= b["days_until_due"] <= days]


def cards_due_soon(
    base: str,
    rates: dict,
    today: date,
    statements: list[dict] | None = None,
    within_days: int = CARD_DUE_SOON_DAYS,
) -> list[dict]:
    """Each live card's unpaid statement due within ``within_days`` or already overdue,
    soonest (most overdue) first."""
    if statements is None:
        statements = unpaid_statements(base, rates, today)
    return [b for b in statements if b["days_until_due"] <= within_days]


def unpaid_statements(base: str, rates: dict, today: date) -> list[dict]:
    """Every live card whose last statement still owes money and has a known due date."""
    out: list[dict] = []
    cards = AccountStore().query_accounts(type="credit")
    # One read of the ledger for every card — `statement_summary` re-reads it per call.
    txns = _Loaded(TransactionStore().all()) if cards else None
    for account in cards:
        summary = statement_summary(account["id"], today=today, transactions_store=txns)
        if summary is None or summary.next_due_date is None or summary.days_until_due is None:
            continue
        if summary.remaining_amount <= 0:
            continue
        out.append(
            {
                "date": summary.next_due_date,
                "label": account.get("name") or "Credit card",
                "account_id": account["id"],
                "amount_base": round(
                    svc.to_base(summary.remaining_amount, summary.currency, base, rates), 2
                ),
                "estimated": summary.due_source != "statement",
                "days_until_due": summary.days_until_due,
            }
        )
    return sorted(out, key=lambda e: e["date"])


class _Loaded:
    """The ledger, read once, in the shape `statement_summary` reads it through."""

    def __init__(self, items: list[dict]):
        self._items = items

    def all(self) -> list[dict]:
        return self._items
