"""Budget/planning-derived Overview figures: envelope progress, goals, month-over-month
category moves, the spend calendar, recurring commitments and the month's cash flow.

Planning already aggregates per-category `assigned`/`activity` server-side. What the widgets
still did in the browser was the SECOND fold on top of it — summing categories into a month
total, differencing this month against last, bucketing raw transactions into days. Those are
here now, so "spent this month" is one number with one definition.

One real bug this fixes on the way: the recurring-commitments total used to add up
`transaction.amount` across scheduled tasks **without converting currency**, so a book with a
USD subscription and an SGD one reported their raw sum as a base-currency figure.

**Lens-aware since RFC 2026-08-22-pluggable-budget-engines.md Phase 4 (Decision G).** The
progress/spend-vs-budget figures (`envelopes`/`safe_to_spend`/the new `headline`) now source
from the book's HOME budget engine's `ViewModel` via `services.budget_lens` instead of
`planning.budget_month()` directly. The zero-based engine's ViewModel is equivalence-tested
against `budget_month()` (`tests/test_budget_engine_equivalence.py`), so for every existing
book (home = zero-based) these numbers are byte-identical to before —
`tests/test_planning_summary.py` pins that safety property explicitly.

`services.budget_lens` is also now the ONE seam `reports.py`'s `spending_summary` report reads
the month's spend-vs-budget picture through
(`home_spending_summary`) — closing a pre-existing split-brain (RFC §8.1): those two surfaces
used to read `services.budgets.spending_summary` (the unrelated per-category-LIMIT system)
while the seven Overview widgets here read envelope/`budget_month` reality, so the two could
disagree about the same month. `goals` (dated envelope targets) and the spend calendar/movers
are genuinely paradigm-neutral or zero-based-native concepts with no equivalent in every
engine — see each helper's own docstring for why it does or doesn't route through the home
lens.
"""

from __future__ import annotations

from calendar import monthrange
from datetime import date

from backend.core.fx import base_currency
from backend.core.user_time import user_today
from backend.db import read_cache
from backend.services import categories as categories_service
from backend.services import ledger as ledger_service
from backend.services import planning as planning_service
from backend.services import targets as targets_service
from backend.services.budget_lens import (
    envelopes_view,
    home_engine_view,
    safe_to_spend_view,
)
from backend.services.fx_cache import load_fx_cache


def _this_month() -> str:
    return user_today().strftime("%Y-%m")


def _prev_month(ym: str) -> str:
    year, month = (int(p) for p in ym.split("-"))
    return f"{year - 1}-12" if month == 1 else f"{year}-{month - 1:02d}"


def _envelope_pace(assigned: float, spent: float, day_of_month: int, days_left: int) -> dict:
    """Per-envelope remaining budget plus its two per-day splits, pure and unit-testable in
    isolation from the engine plumbing above.

    `avg_per_day` divides by `day_of_month` — the SAME "how far through the month" figure
    `budget_aggregates` already derives for `pace_pct` (today's day-of-month for the current
    month, the whole `days_in_month` for any other month), so a past month's average is over
    its full span rather than an elapsed count that no longer means anything.

    `left_per_day` divides `remaining` by `days_left` (also already derived there, inclusive of
    today, 0 once the month is over). It floors at 0 rather than printing a negative once the
    envelope is spent through (`remaining <= 0`) or the month has ended (`days_left == 0`) — a
    "you can still spend $X/day" figure has nothing left to mean at that point.
    """
    remaining = round(assigned - spent, 2)
    avg_per_day = round(spent / day_of_month, 2) if day_of_month > 0 else 0.0
    left_per_day = round(remaining / days_left, 2) if days_left > 0 and remaining > 0 else 0.0
    return {"remaining": remaining, "avg_per_day": avg_per_day, "left_per_day": left_per_day}


def _with_pace(envelopes: dict, day_of_month: int, days_left: int) -> dict:
    """Adds `remaining`/`avg_per_day`/`left_per_day` to every envelope row so the spending-pace
    widget (ADR 2026-07-31-overview-aggregates-are-server-computed) renders them verbatim
    instead of dividing/subtracting money client-side."""
    rows = [
        {**r, **_envelope_pace(r["assigned"], r["spent"], day_of_month, days_left)}
        for r in envelopes["rows"]
    ]
    by_key = {r["key"]: r for r in rows}
    budgeted_rows = [by_key[r["key"]] for r in envelopes["budgeted_rows"]]
    return {**envelopes, "rows": rows, "budgeted_rows": budgeted_rows}


def _payment_envelope_keys(month_view: dict) -> set[str]:
    return {
        c["key"]
        for group in month_view.get("groups") or []
        for c in group.get("categories") or []
        if c.get("is_payment_envelope") and c.get("key")
    }


def _without_payment_envelopes(envelopes: dict, payment_keys: set[str]) -> dict:
    """The envelope progress with every credit-card PAYMENT envelope taken out, rows and totals
    alike. A payment moves money between the user's own accounts — the card's purchases were
    already spent in their own categories — so leaving it in double-counted a month's spend
    (Oct 6,547.73 = 4,027.83 of spending + 2,519.90 of card payment, "181% of budget spent")
    and its funding inflated the budget side too."""
    if not payment_keys:
        return _like_for_like_pct(envelopes)
    dropped = [r for r in envelopes["rows"] if r["key"] in payment_keys]
    rows = [r for r in envelopes["rows"] if r["key"] not in payment_keys]
    budgeted = [r for r in rows if r["assigned"] > 0]
    total_spent = round(sum(r["spent"] for r in rows), 2)
    total_budget = round(envelopes["total_budget"] - sum(r["assigned"] for r in dropped), 2)
    trimmed = {
        **envelopes,
        "rows": rows,
        "budgeted_rows": budgeted,
        "total_spent": total_spent,
        "budgeted_spent": round(sum(r["spent"] for r in budgeted), 2),
        "budgeted_total": round(sum(r["assigned"] for r in budgeted), 2),
        "total_budget": total_budget,
    }
    return _like_for_like_pct(trimmed)


def _like_for_like_pct(envelopes: dict) -> dict:
    """% of budget spent = what the BUDGETED envelopes spent ÷ what they were given. Dividing ALL
    spend (unbudgeted categories included) by the budgeted total read "369% of budget spent" on a
    book whose only budgets were three savings envelopes nobody had spent from."""
    spent = envelopes.get("budgeted_spent") or 0.0
    total = envelopes.get("budgeted_total") or 0.0
    return {**envelopes, "pct_of_budget": round(spent / total * 100, 2) if total > 0 else 0.0}


def _goal_rows() -> list[dict]:
    """The user's savings goals ("save X by date Y", the `targets` store the Goals tools and
    Plan's goals read), hydrated by `services.targets` — progress is the linked accounts'
    live balance when linked, else the stored amount, in base currency. Cancelled goals are
    not shown. `risk` is behind-pace or past-due."""
    rows = []
    for goal in targets_service.list_hydrated():
        if goal["status"] == "cancelled":
            continue
        have = goal["current_base"]
        amount = goal["target_amount_base"]
        rows.append(
            {
                "key": goal["id"],
                "label": goal.get("name") or "",
                "have": round(have, 2),
                "target": round(amount, 2),
                "pct": round(min(100.0, (have / amount) * 100), 2) if amount > 0 else 0.0,
                "by": goal.get("target_date") or None,
                "risk": goal["status"] in ("behind", "past_due"),
                "status": goal["status"],
            }
        )
    return rows


def _goals(month_view: dict) -> list[dict]:
    """Every savings goal, least-funded first: the user's goals (`_goal_rows`) plus any dated
    envelope target. `needs_whole_amount` picks contributions-to-date vs balance for the
    latter — the same flavor rule Plan itself applies.

    The envelope half is zero-based-NATIVE, deliberately not lens-routed: a dated "by X date"
    target (`set_target`/`needed_this_month`) is a zero-based concept with no equivalent anywhere in
    the paradigm-neutral `ViewModel` contract (Decision C never modeled one) — envelope has no
    RTA-adjacent target machinery and 50/30/20 has none either. Always reads zero-based's own
    `budget_month()` regardless of which engine is home, the same way Commitments' funding
    coverage requires envelope to be *configured* rather than *home* (RFC Decision G) — a
    target set before switching lenses stays visible, not silently orphaned. The goals store is
    paradigm-neutral and read as is."""
    rows = _goal_rows()
    for group in month_view.get("groups") or []:
        for c in group.get("categories") or []:
            target = c.get("target") or {}
            if target.get("type") != "by_date":
                continue
            have = float(
                (c.get("assigned_to_date") or 0.0)
                if target.get("needs_whole_amount")
                else (c.get("available") or 0.0)
            )
            amount = float(target.get("amount") or 0.0)
            risk = c.get("status") == "underfunded"
            rows.append(
                {
                    "key": c.get("key") or f"{group.get('id')}:{c.get('label')}",
                    "label": c.get("label") or "",
                    "have": round(have, 2),
                    "target": round(amount, 2),
                    "pct": round(min(100.0, (have / amount) * 100), 2) if amount > 0 else 0.0,
                    "by": target.get("date"),
                    "risk": risk,
                    "status": "behind" if risk else "on_track",
                }
            )
    rows.sort(key=lambda r: r["pct"])
    return rows


def _category_spend(ym: str) -> dict[str, dict]:
    """Expense spend by category for one month — genuinely paradigm-neutral (RFC Decision G:
    `bud-heatmap`/`bud-movers` need only spend-by-category, no envelope/lens concept at all),
    so this is read directly off the ledger + the category registry rather than any engine's
    `ViewModel`. Split-aware (a SPLIT txn's lines land in each line's own category, mirroring
    `services.budgets._spent_by_category`) and base-currency via `ledger.txn_base_amount`/
    `txn_split_base_amount` — never a caller's own FX, never a raw stored snapshot. Categoryless
    (`category=None`) spend is folded into a synthetic "Uncategorized" entry, exactly as
    `budget_month()`'s own `__unsorted__` group does, so a book with no lens-specific coupling
    at all still sees the same "Uncategorized" mover it always did."""
    base = base_currency()
    rates = load_fx_cache()
    cat_rows = categories_service.all_category_rows()
    label_by_id = {r["id"]: r.get("label", r["id"]) for r in cat_rows}
    color_by_id = {r["id"]: r.get("color") for r in cat_rows}

    spend: dict[str, float] = {}
    for t in ledger_service.transactions.all():
        if (
            t.get("deleted")
            or t.get("kind") != "expense"
            or not (t.get("date") or "").startswith(ym)
        ):
            continue
        splits = t.get("splits")
        if splits:
            for s in splits:
                cat = s.get("category") or "__unsorted__"
                spend[cat] = spend.get(cat, 0.0) + ledger_service.txn_split_base_amount(
                    t, s, base, rates
                )
        else:
            cat = t.get("category") or "__unsorted__"
            spend[cat] = spend.get(cat, 0.0) + ledger_service.txn_base_amount(t, base, rates)

    return {
        cat: {
            "label": "Uncategorized" if cat == "__unsorted__" else label_by_id.get(cat, cat),
            "color": (color_by_id.get(cat) or "") if cat != "__unsorted__" else "",
            "spent": round(amt, 2),
        }
        for cat, amt in spend.items()
    }


def _movers(current: dict[str, dict], previous: dict[str, dict]) -> dict:
    """Per-category spend this month vs last, biggest absolute move first — genuinely
    paradigm-neutral (RFC Decision G), sourced from `_category_spend` rather than any engine's
    `ViewModel`."""
    rows = []
    for key in set(current) | set(previous):
        now_spend = current.get(key, {}).get("spent", 0.0)
        prev_spend = previous.get(key, {}).get("spent", 0.0)
        delta = now_spend - prev_spend
        if abs(delta) < 0.5:
            continue
        source = current.get(key) or previous[key]
        rows.append(
            {
                "key": key,
                "label": source.get("label") or "",
                "now": round(now_spend, 2),
                "prev": round(prev_spend, 2),
                "delta": round(delta, 2),
            }
        )
    rows.sort(key=lambda r: abs(r["delta"]), reverse=True)
    return {"rows": rows, "max_abs": round(max((abs(r["delta"]) for r in rows), default=0.0), 2)}


def _day_buckets(ym: str) -> dict:
    """Per-day money in/out for the month — the spend heatmap and the calendar card.

    Already paradigm-neutral (reads raw ledger transactions only, never `budget_month()` or
    any engine) — RFC Decision G's `bud-heatmap` reclassification needed no code change here,
    only the acknowledgement that this was never envelope-coupled to begin with."""
    year, month = (int(p) for p in ym.split("-"))
    days_in_month = monthrange(year, month)[1]
    base = base_currency()
    rates = load_fx_cache()
    buckets = {day: {"in": 0.0, "out": 0.0} for day in range(1, days_in_month + 1)}

    for t in ledger_service.transactions.all():
        if t.get("deleted") or not (t.get("date") or "").startswith(ym):
            continue
        kind = t.get("kind")
        if kind not in ("income", "expense"):
            continue
        try:
            day = int(t["date"][8:10])
        except (ValueError, KeyError, IndexError):
            continue
        if day not in buckets:
            continue
        amount = abs(ledger_service.txn_base_amount(t, base, rates))
        buckets[day]["in" if kind == "income" else "out"] += amount

    days = [
        {
            "day": day,
            "in": round(v["in"], 2),
            "out": round(v["out"], 2),
            "net": round(v["in"] - v["out"], 2),
        }
        for day, v in sorted(buckets.items())
    ]
    return {
        "month": ym,
        "days_in_month": days_in_month,
        # Weekday the 1st falls on, Sunday=0 — the calendar/heatmap leading offset.
        "first_weekday": (date(year, month, 1).weekday() + 1) % 7,
        "days": days,
        "total_in": round(sum(d["in"] for d in days), 2),
        "total_out": round(sum(d["out"] for d in days), 2),
        "max_out": round(max((d["out"] for d in days), default=0.0), 2),
        "max_flow": round(max((max(d["in"], d["out"]) for d in days), default=0.0), 2),
    }


def _recurring() -> dict:
    """Recurring scheduled transactions — now always empty.

    This read the ``create_transaction`` scheduled tasks, and Scheduled Tasks were retired on
    2026-08-02 along with Agents: a recurring transaction is authored as an Automation now.
    Returning the same empty SHAPE rather than dropping the key keeps every caller and the
    Plan summary's contract intact; a caller that renders an empty list renders nothing.

    Wiring a recurring-transaction Automation into this panel is deliberately NOT done here —
    an Automation's instructions are prose, so there is no template to read an amount and
    cadence off, and inventing one would put fabricated numbers in a money summary.
    """
    return {"rows": [], "monthly_total_base": 0.0}


# How far back the Cash flow card looks for a month with money in it when this one has none.
_CASHFLOW_LOOKBACK = 6


def _cashflow_months(yms: list[str]) -> dict[str, dict]:
    """Money in vs out for each of `yms`, recomputed from native legs into the current base, in
    ONE pass over the ledger."""
    base = base_currency()
    rates = load_fx_cache()
    totals = {ym: {"in": 0.0, "out": 0.0, "in_n": 0, "out_n": 0} for ym in yms}
    for t in ledger_service.transactions.all():
        bucket = totals.get((t.get("date") or "")[:7])
        if t.get("deleted") or bucket is None:
            continue
        kind = t.get("kind")
        if kind not in ("income", "expense"):
            continue
        side = "in" if kind == "income" else "out"
        bucket[side] += abs(ledger_service.txn_base_amount(t, base, rates))
        bucket[side + "_n"] += 1
    return {
        ym: {
            "total_in": round(b["in"], 2),
            "total_out": round(b["out"], 2),
            "net": round(b["in"] - b["out"], 2),
            "in_count": b["in_n"],
            "out_count": b["out_n"],
            "max": round(max(b["in"], b["out"], 1.0), 2),
        }
        for ym, b in totals.items()
    }


def _cashflow(ym: str) -> tuple[dict, dict | None]:
    """The month's money in vs out, and — only while the month has none — the newest earlier
    month that does (with its ``month``), so a card does not read "0 in, 0 out" beside a full
    September on the 1st of October (persona QA round 7; Reports' Spending by category already
    falls back the same way)."""
    earlier: list[str] = []
    cursor = ym
    for _ in range(_CASHFLOW_LOOKBACK):
        cursor = _prev_month(cursor)
        earlier.append(cursor)
    by_month = _cashflow_months([ym, *earlier])
    current = by_month[ym]
    if current["in_count"] or current["out_count"]:
        return current, None
    last = next((m for m in earlier if by_month[m]["in_count"] or by_month[m]["out_count"]), None)
    return current, ({"month": last, **by_month[last]} if last else None)


@read_cache.cached(lambda ym=None: ym)
def budget_aggregates(ym: str | None = None) -> dict:
    """Every budget/planning figure the Overview renders, for one month."""
    ym = ym or _this_month()
    year, month = (int(p) for p in ym.split("-"))
    today = user_today()
    days_in_month = monthrange(year, month)[1]
    is_current = (today.year, today.month) == (year, month)
    day_of_month = today.day if is_current else days_in_month
    days_left = (days_in_month - today.day + 1) if is_current else 0

    import time as _time

    _perf_log = __import__("logging").getLogger(__name__)
    _t0 = _time.perf_counter()
    # Always read zero-based's own table — `_goals` (dated targets) has no equivalent in any
    # other engine's ViewModel, and it doubles as the zero-based branch of `safe_to_spend_view`
    # below when zero-based happens to be home (the common case for every existing book).
    table = planning_service.budget_month(ym)
    _t1 = _time.perf_counter()
    vm = home_engine_view(ym)
    _t2 = _time.perf_counter()

    cashflow, cashflow_latest = _cashflow(ym)
    result = {
        "month": ym,
        "base_currency": base_currency(),
        "days_in_month": days_in_month,
        "day_of_month": day_of_month,
        "days_left": days_left,
        # How far through the month we are — the "you're 60% through the month" pace note.
        "pace_pct": round((day_of_month / days_in_month) * 100, 2),
        "envelopes": _with_pace(
            _without_payment_envelopes(envelopes_view(vm), _payment_envelope_keys(table)),
            day_of_month,
            days_left,
        ),
        "goals": _goals(table),
        "movers": _movers(_category_spend(ym), _category_spend(_prev_month(ym))),
        "calendar": _day_buckets(ym),
        "recurring": _recurring(),
        "cashflow": cashflow,
        "cashflow_latest": cashflow_latest,
        # The engine-supplied hero number (RFC Decision G: "the headline LABEL becomes
        # engine-supplied") — Ready to Assign or a savings rate, whichever
        # the home lens actually is. `safe_to_spend` below stays for the existing (zero-based
        # shaped) widget contract.
        "headline": {
            "engine_id": vm.engine_id,
            "label": vm.headline.label,
            "value": vm.headline.value,
            "unit": vm.headline.unit,
            "status": vm.headline.status,
            "help": vm.headline.help,
        },
        "safe_to_spend": safe_to_spend_view(vm, table, days_left),
    }
    _t3 = _time.perf_counter()
    _perf_log.info(
        "PERF budget_aggregates budget_month=%.1fms home_engine_view=%.1fms rest=%.1fms",
        (_t1 - _t0) * 1000,
        (_t2 - _t1) * 1000,
        (_t3 - _t2) * 1000,
    )
    return result
