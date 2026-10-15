"""Which credit cards a spending-based figure cannot see yet.

A savings rate, a monthly burn and a runway are all folds over the ledger's expense rows. A card
whose purchases were never imported contributes none of them, so a new book with a salary, a
bank account and an empty credit card reads "85% saved" and "12.9 months of runway": true to
the rows it has, wrong about the person (persona QA round 5, 2026-10-14). Every such figure
therefore carries `cards_missing_spending` — the names of the cards with no activity in the
figure's own period — and its screen says "card spending not included yet".

Only a purchase counts as activity: a card's Starting Balance and a payment toward it prove
nothing about whether its spending was imported.
"""

from __future__ import annotations

from backend.services import ledger as svc


def cards_without_activity(
    date_from: str, date_to: str, txns: list[dict] | None = None
) -> list[str]:
    """Names of the open credit cards with no purchase (expense) dated in
    ``[date_from, date_to]`` (ISO dates, inclusive)."""
    cards = [a for a in svc.accounts.query_accounts() if a.get("type") == "credit"]
    if not cards:
        return []
    if txns is None:
        txns = svc.transactions.all()
    openings = {a.get("opening_txn_id") for a in cards}
    touched: set[str] = set()
    for t in txns:
        if t.get("deleted") or t.get("kind") != "expense" or t.get("id") in openings:
            continue
        if not (date_from <= (t.get("date") or "")[:10] <= date_to):
            continue
        touched.update(a for a in (t.get("from_account_id"), t.get("to_account_id")) if a)
    return [a.get("name") or "Credit card" for a in cards if a["id"] not in touched]
