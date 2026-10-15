"""
recurring_baseline — compose the forward cash-flow baseline from classified streams.

The integration layer between pure detection (``services/recurring``), user state
(``db/recurring_stream_store``) and the projection (``services/cashflow``). Splits the ledger
into the three buckets the RFC defines and hands ``cashflow`` a baseline it can walk forward:

  * ``streams``    — matured detected streams (minus dismissed) ∪ declared streams. Projected
                     on their OWN calendar months by ``recurring.stream_month_amounts``. A
                     declared stream REPLACES the history it restates (``_fold_history_into_declared``).
  * ``variable_*`` — the flat monthly average of what is left after streams and one-offs are
                     removed: genuinely irregular everyday spend, which a forecast must still
                     count or it understates the month badly.
  * ``one_offs``   — excluded from the forward baseline, reported so the exclusion is visible
                     rather than silent.

Only ``matured`` streams (≥3 occurrences) are projected. A ``tentative`` 2-occurrence stream is
returned in ``candidates`` for the user to confirm, but never silently drives a projection —
two hits is a coincidence as often as a pattern, and a wrong confident number is worse than an
honest "we're not sure yet". Confirming one promotes it; that is the escape hatch for a new
salary that has only been paid twice.
"""

from __future__ import annotations

from calendar import monthrange
from datetime import date
from math import ceil

from backend.core.user_time import user_today
from backend.db.recurring_stream_store import RecurringStreamStore
from backend.db.transaction_store import txn_text
from backend.services import ledger as svc
from backend.services import payee_registry, recurring
from backend.services.budgets import is_spending_expense

RESIDUAL_MONTHS = 3  # trailing full months AVERAGED for the variable baseline
UNCATEGORIZED = "__uncategorized__"

# A category "recurs" if it shows up in enough DISTINCT MONTHS of the (long) coverage window.
# The thresholds differ by direction on the accounting PRUDENCE convention — do not overstate
# income, do not understate expenses. Both possible errors point at the same danger (a forecast
# that flatters you), so each side is allowed to err the safe way:
#   income  — STRICT: a category must appear in EVERY observed month. An inflow you cannot show
#             is habitual should not fund a projection.
#   expense — LENIENT: half the months is enough. Real spending categories skip a month all the
#             time, and dropping one makes you look richer than you are.
INCOME_COVERAGE_RATIO = 1.0
EXPENSE_COVERAGE_RATIO = 0.5


def _month_bounds(year: int, month: int) -> tuple[str, str]:
    return f"{year:04d}-{month:02d}-01", f"{year:04d}-{month:02d}-{monthrange(year, month)[1]:02d}"


def _window(today: date, months: int, *, include_current: bool = False) -> tuple[str, str]:
    """The trailing `months` FULL calendar months (current month excluded — it is partial).

    `include_current` extends the end to TODAY, and DETECTION uses it while the residual
    average does not. The distinction matters in both directions:

    * for AVERAGING, a half-finished month understates the monthly rate, so it is excluded;
    * for DETECTION, a payment that already landed this month is the best evidence the stream
      is still alive. Excluding it made a monthly stream look stale purely because of the day
      of the month: checked on the 27th, rent paid on the 1st had a last-known occurrence of
      *last* month — 56 days back, past the 52-day staleness cut — so a perfectly live stream
      was written off, even though this month's rent was paid 26 days ago.
    """
    if include_current:
        sy, sm = recurring._add_months(today.year, today.month, -(months - 1))
        return _month_bounds(sy, sm)[0], today.isoformat()
    ey, em = recurring._add_months(today.year, today.month, -1)
    sy, sm = recurring._add_months(ey, em, -(months - 1))
    return _month_bounds(sy, sm)[0], _month_bounds(ey, em)[1]


def _load_rows(
    date_from: str, date_to: str, base: str, rates: dict, all_txns: list[dict] | None = None
) -> list[dict]:
    """Ledger → the normalized row shape `recurring.detect_streams` consumes.

    `all_txns` (optional): an already-loaded, already-deleted-filtered list. `query_txns` is
    NOT a SQL query — it calls `.all()` and filters in Python — so each of the four calls this
    function makes per `resolve_baseline` was a full reparse of the whole book. See
    `cashflow.build_projection`, which loads once and threads it down.
    """
    rows: list[dict] = []
    payee_names = payee_registry.payee_names()
    for kind, direction in (("income", "in"), ("expense", "out")):
        if all_txns is None:
            matched = svc.transactions.query_txns(kind=kind, date_from=date_from, date_to=date_to)
        else:
            matched = [
                t
                for t in all_txns
                if t.get("kind") == kind and date_from <= t.get("date", "") <= date_to
            ]
        for t in matched:
            # A security buy is cash→asset, not spending — same exclusion the old
            # expense average applied, kept so a lumpy stock buy is never "recurring spend".
            if kind == "expense" and not is_spending_expense(t):
                continue
            when = recurring._parse(t.get("date"))
            amount = abs(svc.txn_base_amount(t, base, rates))
            if not when or amount <= 0:
                continue
            rows.append(
                {
                    "id": t.get("id"),
                    "date": when,
                    "amount": amount,
                    "payee_id": t.get("payee_id") or "",
                    "category": t.get("category") or UNCATEGORIZED,
                    # The payee's own name: the bank's wording until the user renames it.
                    "label": payee_names.get(t.get("payee_id") or "")
                    or txn_text(t)
                    or "(no description)",
                    "direction": direction,
                }
            )
    return rows


def recurring_categories(rows: list[dict], direction: str) -> tuple[set[str], int]:
    """Which CATEGORIES of `direction` recur, judged by how many months they appear in.

    This replaced a magnitude heuristic (drop any transaction ≥15% of its pool), and the
    reason is worth keeping: magnitude was a proxy for a question it could not actually ask.
    Measured against a representative book it excluded three equity-sale proceeds *and* missed
    three more — a S$4,200 pass-through at 8.5% of the pool and a share sale mislabelled
    "Salary" at 3.8% — because it was testing size, never whether the thing recurs.

    The right question, and the one this asks: does this KIND of flow happen every month?
    That is the property that makes averaging legitimate in the first place. Individual
    grocery or restaurant transactions never repeat — different merchant, amount and day —
    yet "Daily Meals" appears every single month, so the POPULATION recurs even though no
    member of it does. A stock sale has no population: its category shows up once.

    Doing it per-CATEGORY rather than per-transaction is what makes that visible, and it
    reuses classification the user already did rather than re-deriving it statistically.
    Per-transaction tests were measured and fail badly in both directions: "payee repeated"
    keeps every stock sale (the employer also pays salary) while deleting 59% of real
    spending; "payee+amount repeated" correctly drops 100% of one-off income but also 88% of
    expenses, because a unique amount is precisely what everyday spending looks like.

    Returns (recurring_category_ids, months_observed). UNCATEGORIZED always counts as
    recurring: "we cannot classify this" is not evidence of a windfall, and silently dropping
    money we failed to understand is the worse error.
    """
    months_by_cat: dict[str, set[str]] = {}
    observed: set[str] = set()
    for row in rows:
        if row["direction"] != direction:
            continue
        ym = f"{row['date'].year:04d}-{row['date'].month:02d}"
        observed.add(ym)
        months_by_cat.setdefault(row["category"], set()).add(ym)
    if not observed:
        return set(), 0
    ratio = INCOME_COVERAGE_RATIO if direction == "in" else EXPENSE_COVERAGE_RATIO
    # A book with ONE observed month passes everything (1/1 ≥ any ratio) — deliberately, so a
    # brand-new user's first paycheck is counted rather than dismissed as unproven.
    needed = max(1, ceil(ratio * len(observed)))
    keep = {cat for cat, months in months_by_cat.items() if len(months) >= needed}
    keep.add(UNCATEGORIZED)
    return keep, len(observed)


def _split_by_coverage(rows: list[dict], keep_categories: set[str]) -> tuple[list, list]:
    """Partition a residual pool into (recurring populations, one-offs) by category."""
    kept, one_offs = [], []
    for row in rows:
        (kept if row["category"] in keep_categories else one_offs).append(row)
    return kept, one_offs


def _monthly_average(rows: list[dict], months: int) -> float:
    """Divide by months WITH DATA (never the window width) so sparse history isn't deflated —
    the one behaviour worth preserving verbatim from the old rolling average."""
    if not rows:
        return 0.0
    distinct = len({(r["date"].year, r["date"].month) for r in rows}) or 1
    return round(sum(r["amount"] for r in rows) / min(distinct, months), 2)


def resolve_baseline(
    base: str, rates: dict, today: date | None = None, all_txns: list[dict] | None = None
) -> dict:
    """The whole classification, in one pass. Pure read — writes nothing."""
    today = today or user_today()
    det_from, det_to = _window(today, recurring.DETECTION_MONTHS, include_current=True)
    detection_rows = _load_rows(det_from, det_to, base, rates, all_txns)
    detected = recurring.detect_streams(detection_rows, today)

    store = RecurringStreamStore()
    overlays = store.overlays()
    active, candidates = [], []
    for stream in detected:
        status = (overlays.get(stream["key"]) or {}).get("status", "")
        stream = {**stream, "status": status or "detected"}
        if status == "dismissed":
            candidates.append(stream)
        elif stream.get("ended") and status != "confirmed":
            # Stopped happening — a superseded salary, a cancelled subscription. Surfaced as
            # a candidate so it is visible, never projected forward.
            candidates.append({**stream, "status": "ended"})
        elif status == "confirmed" or stream["confidence"] == "matured":
            active.append(stream)
        else:
            candidates.append(stream)

    declared = [_declared_to_stream(row, base, rates) for row in store.declared()]

    # Residual = the RESIDUAL window minus anything already claimed by an ACTIVE stream —
    # detected or declared (a declaration adopts the history it restates).
    res_from, res_to = _window(today, RESIDUAL_MONTHS)
    window_rows = _load_rows(res_from, res_to, base, rates, all_txns)
    active = _fold_history_into_declared(active, declared, window_rows, today) + declared
    claimed = {tid for s in active for tid in s.get("txn_ids") or []}
    residual = [r for r in window_rows if r["id"] not in claimed]

    # Coverage is judged over the LONG detection window but the amount is averaged over the
    # SHORT one: "does this kind of flow recur" wants as many months as possible, while "how
    # much per month" should reflect current life, not a year-old lifestyle.
    #
    # It reads ALL detection rows, INCLUDING ones already claimed by a stream. Excluding them
    # collapses the month universe to whatever the leftovers happen to span: with a salary
    # claimed, the only income months remaining were the windfall's own, so three stock sales
    # in a single month scored 1/1 — full coverage — and were kept as "recurring". The
    # denominator has to be every month with activity, not every month with residue.

    variable, one_offs, coverage = {}, [], {}
    for direction in ("in", "out"):
        keep_cats, months_observed = recurring_categories(detection_rows, direction)
        coverage[direction] = {"months_observed": months_observed, "categories": len(keep_cats)}
        pool = [r for r in residual if r["direction"] == direction]
        kept, dropped = _split_by_coverage(pool, keep_cats)
        variable[direction] = _monthly_average(kept, RESIDUAL_MONTHS)
        one_offs.extend(dropped)

    return {
        "streams": active,
        "candidates": candidates,
        "variable_in": variable["in"],
        "variable_out": variable["out"],
        "one_offs": [
            {
                "id": r["id"],
                "date": r["date"].isoformat(),
                "label": r["label"],
                "amount_base": round(r["amount"], 2),
                "direction": r["direction"],
            }
            for r in sorted(one_offs, key=lambda r: -r["amount"])
        ],
        "detection_window": {"from": det_from, "to": det_to},
        "residual_window": {"from": res_from, "to": res_to},
        "coverage": coverage,
    }


def upcoming_income(
    days: int, base: str, rates: dict, today: date | None = None, all_txns: list[dict] | None = None
) -> list[dict]:
    """Dated money-IN occurrences of the book's active recurring income streams over the next
    ``days`` days, soonest first — the same streams the cash-flow projection walks (a declared
    salary, a confirmed or matured detected one), so a salary shows on the day it lands. An
    occurrence dated today or earlier has already posted and is in the ledger, so it is not
    listed again."""
    return _upcoming("in", "Recurring income", days, base, rates, today, all_txns)


def upcoming_bills(
    days: int, base: str, rates: dict, today: date | None = None, all_txns: list[dict] | None = None
) -> list[dict]:
    """The money-OUT twin of ``upcoming_income``: dated occurrences of the active recurring
    expense streams (rent, a subscription) over the next ``days`` days, soonest first. Streams
    are built from income and expense rows only, so a card payment or a loan transfer is never
    one — a card's bill is ``upcoming_bills.card_bills``' to report."""
    return _upcoming("out", "Recurring bill", days, base, rates, today, all_txns)


def _upcoming(
    direction: str,
    default_label: str,
    days: int,
    base: str,
    rates: dict,
    today: date | None,
    all_txns: list[dict] | None,
) -> list[dict]:
    today = today or user_today()
    through = date.fromordinal(today.toordinal() + days)
    events = [
        {
            "date": d.isoformat(),
            "label": stream.get("label") or default_label,
            "cadence": stream.get("cadence") or "",
            "amount_base": stream["amount_base"],
        }
        for stream in resolve_baseline(base, rates, today, all_txns)["streams"]
        if stream.get("direction") == direction and float(stream.get("amount_base") or 0) > 0
        for d in recurring.occurrences_between(stream, today, through)
    ]
    return sorted(events, key=lambda e: e["date"])


def _is_live(stream: dict, today: date) -> bool:
    """A declared stream that applies NOW. One starting later (a raise) is a CHANGE to what
    history shows, not a restatement of it; one already ended no longer describes the book."""
    start, end = stream.get("start_date") or "", stream.get("end_date") or ""
    return (not start or start <= today.isoformat()) and (not end or end >= today.isoformat())


def _near(amount: float, anchor: float) -> bool:
    return recurring._matches_a_known_amount(amount, [anchor])


def _fold_history_into_declared(
    detected: list[dict], declared: list[dict], residual: list[dict], today: date
) -> list[dict]:
    """Let a declared stream REPLACE the history it restates, instead of stacking on it.

    A declared stream is projected on its own calendar, so history that already shows the same
    flow must not ALSO feed the projection — a S$5,400 salary declared beside a S$5,404 salary
    txn projected S$10.8K a month (the txn sat in the variable average, the declaration on top).
    History lives in two places, both folded into the declaration, which ADOPTS the txn ids so
    the one stream owns them everywhere it is read:

      * a DETECTED active stream of the same direction and cadence within the amount band —
        dropped from the projection (the declaration is the user's word for it);
      * RESIDUAL rows of the same direction within the band — at most one per cadence
        occurrence per month (the closest amounts), so a coincidentally equal small purchase
        cannot be swallowed in bulk.

    Direction-blind, so a declared rent replaces rent history exactly as a salary does. A
    declaration starting later (a raise) is a CHANGE to history and one already ended no longer
    describes it — neither folds anything (``_is_live``). Fills the declared dicts' ``txn_ids``
    in place; returns the detected streams that remain."""
    live = [d for d in declared if _is_live(d, today)]
    superseded: set[str] = set()
    for d in live:
        for s in detected:
            if (
                s["key"] not in superseded
                and s.get("direction") == d["direction"]
                and s.get("cadence") == d["cadence"]
                and _near(s["amount_base"], d["amount_base"])
            ):
                superseded.add(s["key"])
                d["txn_ids"].extend(s.get("txn_ids") or [])
    kept = [s for s in detected if s["key"] not in superseded]

    taken = {tid for s in kept + live for tid in s.get("txn_ids") or []}
    for d in live:
        per_month = ceil(recurring.CADENCES[d["cadence"]][4])
        by_month: dict[tuple[int, int], list[dict]] = {}
        for r in residual:
            if r["id"] in taken or r["direction"] != d["direction"]:
                continue
            if _near(r["amount"], d["amount_base"]):
                by_month.setdefault((r["date"].year, r["date"].month), []).append(r)
        for rows in by_month.values():
            rows.sort(key=lambda r: abs(r["amount"] - d["amount_base"]))
            for r in rows[:per_month]:
                d["txn_ids"].append(r["id"])
                taken.add(r["id"])
    return kept


def _declared_to_stream(row: dict, base: str, rates: dict) -> dict:
    """A declared row wears the same shape as a detected stream so the projection walk and
    every consumer stay single-path."""
    currency = (row.get("currency") or base).upper()
    amount = float(row.get("amount") or 0)
    amount_base = amount if currency == base.upper() else svc.to_base(amount, currency, base, rates)
    cadence = row.get("cadence") or "monthly"
    per_month = recurring.CADENCES.get(cadence, recurring.CADENCES["monthly"])[4]
    return {
        "key": row["id"],
        "direction": row.get("direction") or "out",
        "payee_id": "",
        "label": row.get("label") or "Declared stream",
        "cadence": cadence,
        "amount_base": round(amount_base, 2),
        "monthly_equivalent": round(amount_base * per_month, 2),
        "anchor_date": row.get("anchor_date") or "",
        "last_date": row.get("anchor_date") or "",
        "next_expected_date": row.get("anchor_date") or "",
        "start_date": row.get("start_date") or "",
        "end_date": row.get("end_date") or "",
        "occurrences": 0,
        "confidence": "declared",
        "source": "declared",
        "status": "declared",
        "note": row.get("note") or "",
        "txn_ids": [],
    }
