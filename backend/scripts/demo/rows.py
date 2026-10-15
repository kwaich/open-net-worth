"""Row builders for the demo ledger.

Thin constructors for the four transaction shapes the generators emit. They exist so a
generator reads as a story ("groceries on the card") rather than as leg plumbing, and so
the leg conventions (an expense is a FROM leg, income is a TO leg, a transfer needs both
amounts) are stated once instead of in twenty places.

Amounts are always positive and native to their account's currency — the ledger choke
converts and stamps the base-currency snapshot itself.
"""

from __future__ import annotations

from datetime import date


def _row(**kw) -> dict:
    row = {
        "category": None,
        "note": "",
        "tags": [],
        "source": "manual",
        "cleared": True,
        "needs_review": False,
    }
    row.update(kw)
    return row


def expense(d: date, account: str, amount: float, description: str, category: str, **kw) -> dict:
    return _row(
        date=d.isoformat(),
        kind="expense",
        description=description,
        category=category,
        from_account_id=account,
        from_amount=round(float(amount), 2),
        **kw,
    )


def income(d: date, account: str, amount: float, description: str, category: str, **kw) -> dict:
    return _row(
        date=d.isoformat(),
        kind="income",
        description=description,
        category=category,
        to_account_id=account,
        to_amount=round(float(amount), 2),
        **kw,
    )


def transfer(
    d: date,
    src: str,
    dst: str,
    amount: float,
    description: str,
    to_amount: float | None = None,
    **kw,
) -> dict:
    """A move between two own accounts. ``to_amount`` differs from ``amount`` only when the
    two legs are denominated differently — the figure and its unit belong to the same leg."""
    return _row(
        date=d.isoformat(),
        kind="transfer",
        description=description,
        from_account_id=src,
        from_amount=round(float(amount), 2),
        to_account_id=dst,
        to_amount=round(float(amount if to_amount is None else to_amount), 2),
        **kw,
    )


def buy(d: date, account: str, amount: float, description: str, **kw) -> dict:
    """Cash leaving a brokerage/exchange account to settle a purchase."""
    return _row(
        date=d.isoformat(),
        kind="investment",
        description=description,
        from_account_id=account,
        from_amount=round(float(amount), 2),
        **kw,
    )


def sell(d: date, account: str, amount: float, description: str, **kw) -> dict:
    """Sale proceeds settling into a brokerage/exchange account."""
    return _row(
        date=d.isoformat(),
        kind="investment",
        description=description,
        to_account_id=account,
        to_amount=round(float(amount), 2),
        **kw,
    )


def split(d: date, account: str, description: str, lines: list[dict], **kw) -> dict:
    """One charge broken across categories. ``lines`` = [{category, amount, note?}]."""
    total = round(sum(float(x["amount"]) for x in lines), 2)
    return _row(
        date=d.isoformat(),
        kind="expense",
        description=description,
        from_account_id=account,
        from_amount=total,
        splits=[
            {
                "category": line["category"],
                "amount": round(float(line["amount"]), 2),
                "note": line.get("note", ""),
            }
            for line in lines
        ],
        **kw,
    )
