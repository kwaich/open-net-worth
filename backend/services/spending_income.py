"""What came in during a window, split into money EARNED and money PAID BACK.

The ledger books every arriving payment as ``kind=income``, so a friend repaying a dinner or a
loan you service on someone's behalf lands in the same total as a salary — a live weekly report
printed the lot as "Came in", nearly a quarter of it money paid back (owner, 2026-10-05). The
category says which is which: an income row under an INCOME-kind category (Salary, Dividends)
was earned; under an EXPENSE-kind category (Dining out, a proxy loan, a split bill) it is money
paid back toward spending. An uncategorised income row counts as earned and is reported in
``unsorted`` so the reader can tell.

``total`` is the same set ``get_transactions`` sums into ``summary.total_income_base``; every
amount is ``txn_base_amount`` (the ledger's own base-currency rule).
"""

from __future__ import annotations

from datetime import date

from backend.db.category_store import CategoryStore
from backend.models.transaction import SPLIT
from backend.services import ledger as ledger_service

MAX_ITEMS = 20


def income_kinds() -> set[str]:
    return {c["id"] for c in CategoryStore().where(kind="income") if c.get("id")}


def parts(t: dict, amount: float) -> list[tuple[str, float]]:
    """(category_id, amount) — a split row's money shared across its lines' categories."""
    if (t.get("category") or "") != SPLIT:
        return [(t.get("category") or "", amount)]
    leg = ledger_service.allocated_leg_amount(t)
    lines = [ln for ln in t.get("splits") or [] if isinstance(ln, dict)]
    if not leg or not lines:
        return [(SPLIT, amount)]
    return [
        (ln.get("category") or "", abs(float(ln.get("amount") or 0.0)) / leg * amount)
        for ln in lines
    ]


def income_split(start: date, end: date, base: str, rates: dict, maps: dict) -> dict:
    rows = ledger_service.transactions.query_txns(
        kind="income", date_from=start.isoformat(), date_to=end.isoformat()
    )
    earned_kinds, cats = income_kinds(), maps["categories"]
    total = earned = unsorted = 0.0
    by_cat: dict[str, dict] = {}
    paid_back: list[dict] = []
    for t in rows:
        amount = ledger_service.txn_base_amount(t, base, rates)
        total += amount
        back = 0.0
        for cat_id, part in parts(t, amount):
            if not cat_id:
                unsorted += part
                earned += part
            elif cat_id in earned_kinds:
                earned += part
                slot = by_cat.setdefault(
                    cat_id, {"category": cats.get(cat_id) or "", "amount": 0.0}
                )
                slot["amount"] += part
            else:
                back += part
        if back:
            paid_back.append(
                {
                    "txn_id": t["id"],
                    "date": t["date"],
                    "payee": maps["payees"].get(t.get("payee_id") or "") or "",
                    "category": cats.get(t.get("category") or "")
                    or ("Split" if t.get("category") == SPLIT else ""),
                    "amount": round(back, 2),
                }
            )
    paid_back.sort(key=lambda r: -r["amount"])
    return {
        "total": round(total, 2),
        "earned": {
            "total": round(earned, 2),
            "unsorted": round(unsorted, 2),
            "by_category": sorted(
                ({**v, "amount": round(v["amount"], 2)} for v in by_cat.values()),
                key=lambda v: -v["amount"],
            ),
        },
        "paid_back": {
            "total": round(total - earned, 2),
            "count": len(paid_back),
            "items": paid_back[:MAX_ITEMS],
        },
    }
