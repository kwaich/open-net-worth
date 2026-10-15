"""Which spending is a BILL, and which bills went missing — the recurring half of
``spending_breakdown``, split out to keep that module readable."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Protocol

from backend.models.transaction import SPLIT
from backend.services import ledger as ledger_service
from backend.services import recurring


class _Rows(Protocol):
    rows: list


def bill_categories(
    raw: list[dict], claimed: dict[str, str], since: str, base: str, rates: dict
) -> dict[str, str]:
    """{category_id: cadence} for every category that HOSTS a recurring bill: since ``since``
    at least 3 of its expense rows were claimed by recurring streams and those rows carry at
    least a quarter of its money. Rent qualifies even in a moving year full of deposits, fees
    and a new landlord (a minority of its rows, but a large share of its money); Groceries with a small monthly
    membership does not. Every row in such a category is then a bill from its first payment."""
    money: dict[str, list[float]] = {}
    hits: dict[str, int] = {}
    cadence: dict[str, str] = {}
    for t in raw:
        cat = t.get("category") or ""
        if not cat or cat == SPLIT or (t.get("date") or "") < since:
            continue
        amount = ledger_service.txn_base_amount(t, base, rates)
        slot = money.setdefault(cat, [0.0, 0.0])
        slot[0] += amount
        if t["id"] in claimed:
            slot[1] += amount
            hits[cat] = hits.get(cat, 0) + 1
            cadence.setdefault(cat, claimed[t["id"]])
    return {
        c: cadence[c]
        for c, (total, billed) in money.items()
        if hits.get(c, 0) >= 3 and total > 0 and billed / total >= 0.25
    }


def bills_due_not_seen(
    streams: list[dict], book: _Rows, start: date, end: date, today: date
) -> list[dict]:
    """Active detected OUT streams with an occurrence due inside the window and none seen.

    "Seen" is any of the stream's own rows within half the cadence's shortest gap of the due
    date — a rent paid three days early is the occurrence, not a miss. Declared streams claim
    no transactions, so they cannot be checked and are left out (`_notes` says so).
    """
    by_id = {r.id: r for r in book.rows}
    out = []
    for s in streams:
        ids = [i for i in s.get("txn_ids") or [] if i in by_id]
        last = recurring._parse(s.get("last_date"))
        if not ids or not last or s["cadence"] not in recurring.CADENCES:
            continue
        first = min(by_id[i].date for i in ids)
        lo, hi = max(start, date.fromisoformat(first)), min(end, today)
        slack = timedelta(days=recurring.CADENCES[s["cadence"]][0] // 2)
        due = _occurrences(last, s["cadence"], lo, hi)
        seen = [date.fromisoformat(by_id[i].date) for i in ids]
        if due and not any(abs(due[0] - d) <= slack for d in seen):
            out.append(
                {
                    "label": s["label"],
                    "cadence": s["cadence"],
                    "expected_date": due[0].isoformat(),
                    "amount": round(float(s.get("amount_base") or 0), 2),
                }
            )
    return sorted(out, key=lambda b: (b["expected_date"], -b["amount"]))


def _occurrences(anchor: date, cadence: str, lo: date, hi: date) -> list[date]:
    """The stream's calendar dates inside [lo, hi], stepping from a known occurrence."""
    found, i = [], 0
    while i > -2000 and recurring._step(anchor, cadence, i) >= lo:
        found.append(recurring._step(anchor, cadence, i))
        i -= 1
    i = 1
    while i < 2000 and recurring._step(anchor, cadence, i) <= hi:
        found.append(recurring._step(anchor, cadence, i))
        i += 1
    return sorted(d for d in found if lo <= d <= hi)


def worth_reporting(missing: list[dict], usual_day_to_day: float) -> list[dict]:
    """Drop a 'missing' bill too small to tell anyone about — a card's cents-sized conversion
    fee is a detected monthly stream, not a bill (1% of a usual period's day-to-day spend)."""
    floor = 0.01 * usual_day_to_day
    return [b for b in missing if b["amount"] >= floor]


def breakdown_notes(
    first_spend: str | None,
    baseline: list[tuple[date, date]],
    requested: int,
    starts: list[date | None],
    detection_from: date,
    streams: list[dict],
) -> list[str]:
    notes = []
    if not first_spend:
        notes.append("No expense history yet, so there is nothing to compare against.")
    elif len(baseline) < requested:
        notes.append(
            f"The book's first expense is {first_spend}, so 'usual' averages {len(baseline)} "
            f"period(s) rather than the {requested} requested."
        )
    earliest = min([d for d in starts if d] + [w[0] for w in baseline])
    if earliest < detection_from:
        notes.append(
            f"Recurring-bill detection only covers {detection_from.isoformat()} onward, so "
            "bills earlier than that are counted as day-to-day spending."
        )
    if not streams:
        notes.append(
            "No recurring bills are detected yet (a stream needs 3 occurrences at a regular "
            "interval), so everything is counted as day-to-day."
        )
    if any(s.get("source") == "declared" for s in streams):
        notes.append(
            "Declared recurring streams claim no transactions, so they neither separate bills "
            "from day-to-day spending nor appear in bills_due_not_seen."
        )
    return notes
