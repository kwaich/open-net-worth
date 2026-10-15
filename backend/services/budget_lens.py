"""Adapts a `BudgetEngine`'s `ViewModel` (`services.budget_engines`) into the pre-Phase-4
widget/report shapes — the ONE shared seam `planning_summary.budget_aggregates()` (the
Overview widgets) and `reports.py`'s `spending_summary` report read through now (RFC
2026-08-22-pluggable-budget-engines.md, Decision G / Phase 4). The daily briefing no longer
does: it prints the home method's own month view (`services.briefing_context`).

**The split-brain this closes (§8.1):** before Phase 4, the seven Overview widgets read
envelope/`budget_month()` reality while `reports.py`'s `spending_summary` and the daily
briefing read `services.budgets.spending_summary` — an unrelated per-category-LIMIT system
that could disagree about the same month. All three now read `home_engine_view`/
`engine_progress_rows`/`home_spending_summary` below.

**The safety property**: zero-based's `ViewModel` is equivalence-tested against
`budget_month()` (`tests/test_budget_engine_equivalence.py`), so `engine_progress_rows`'s
zero-based branch reproduces `budget_month()`'s own `assigned`/`activity` numbers exactly —
`tests/test_planning_summary.py` pins the resulting `budget_aggregates()` output as an
explicit oracle diff against the pre-Phase-4 formula, not just "trust the equivalence test"."""

from __future__ import annotations

from calendar import monthrange

from backend.core.fx import base_currency
from backend.core.user_time import user_today
from backend.services import budgets as budgets_service
from backend.services import ledger as ledger_service
from backend.services.budget_engines import service as budget_engines_service
from backend.services.budget_engines.types import ViewModel
from backend.services.fx_cache import load_fx_cache

_SPEND_EPSILON = 0.005

# engine_id -> (budget-like measure key, spend-like measure key) on that engine's own Row.
# Only zero-based has a natural per-category MONEY pair (RFC Decision C: each engine's
# Row.measures use its OWN paradigm's key names, not one forced vocabulary — see
# zero_based.py's assigned/activity). 50/30/20 budgets by percent-of-income, not
# per-category money, so it is deliberately absent here.
_PROGRESS_MEASURE_KEYS: dict[str, tuple[str, str]] = {
    "zero_based": ("assigned", "activity"),
    # 50/30/20 is envelope-based since 2026-09-26: each row is an envelope with its month's
    # spend (positive) and an optional monthly CAP — the per-envelope budget. An uncapped
    # envelope reads as spend with no budget, never a fabricated one.
    "proportional": ("cap", "activity"),
}


def _spent(activity: float) -> float:
    """Envelope-style spend from a SIGNED net-activity figure: outflow negative, so spend is
    the negated magnitude when activity is negative, 0 otherwise (a net-positive category —
    e.g. a refund — reads as no spend, not negative spend)."""
    return -activity if activity < 0 else 0.0


def home_engine_view(ym: str, engine_id: str | None = None) -> ViewModel:
    """The `ViewModel` for `engine_id` (default: the book's HOME lens) at `ym` — the one seam
    the Overview budget widgets and the spending-summary report read through, so they can no longer disagree about which paradigm's numbers are "the" budget
    for the month (RFC 2026-08-22-pluggable-budget-engines.md §8.1, the split-brain this
    closes)."""
    return budget_engines_service.get_view(engine_id, ym)


def _measure_value(measures: list, key: str) -> float:
    return next((m.amount_base or 0.0 for m in measures if m.key == key), 0.0)


def _unmapped_pair(unmapped) -> dict:
    # `budget_month()`'s own synthetic Uncategorized category carries `key=None`; the pre-Phase-4
    # `_envelopes()` fell back to the LABEL for a keyless row (`c.get("key") or
    # c.get("label")`) — replicated here so a row's `key` is exactly what it always was.
    return {
        "key": unmapped.label,
        "label": unmapped.label,
        "color": "",
        "assigned": 0.0,
        "spent": _spent(unmapped.amount_base),
    }


def engine_progress_rows(vm: ViewModel) -> list[dict]:
    """`{key, label, color, assigned, spent}` pairs — the shared per-category budget-vs-spend
    adapter every "how am I doing this month" surface (the Overview widgets, the
    spending-summary report) reads through, so they read ONE reality.

    Zero-based pairs each category's assigned with its activity; 50/30/20 each envelope's cap
    (its budget, when set) with its spend (`_proportional_pairs`) — an uncapped envelope is
    spend with no budget, never a forced/fabricated number (CLAUDE.md: computed, never
    fabricated). It used to contribute no rows at all, which left every budget widget empty
    for a 50/30/20 book.

    Zero-based's ONLY: its rows carry `budget_month()`'s own `assigned`/`activity` numbers,
    unchanged (the equivalence test's safety property), and its `Unmapped` (the synthetic
    "Uncategorized" category, `category=None` spend) is folded back in as a plain row at the exact
    position `budget_month()`'s own `__unsorted__` group sat — between the real groups and
    `__unbudgeted__` — so a stable sort on `spent` can never reorder rows relative to the
    pre-Phase-4 output."""
    keys = _PROGRESS_MEASURE_KEYS.get(vm.engine_id)
    if keys is None:
        return []
    budget_key, spend_key = keys
    fold_unmapped = vm.engine_id == "zero_based"

    def _pair(row) -> dict:
        spent = _measure_value(row.measures, spend_key)
        return {
            "key": row.id,
            "label": row.label,
            "color": row.color or "",
            "assigned": _measure_value(row.measures, budget_key),
            "spent": _spent(spent) if fold_unmapped else spent,
        }

    if vm.engine_id == "proportional":
        return _proportional_pairs(vm, _pair)

    pairs: list[dict] = []
    unmapped_pending = fold_unmapped and vm.unmapped is not None
    for group in vm.groups:
        if unmapped_pending and group.id == "__unbudgeted__":
            pairs.append(_unmapped_pair(vm.unmapped))
            unmapped_pending = False
        pairs.extend(_pair(row) for row in group.rows)
    if unmapped_pending:
        pairs.append(_unmapped_pair(vm.unmapped))
    return pairs


def _proportional_pairs(vm: ViewModel, pair) -> list[dict]:
    """50/30/20's rows: every envelope in a counted group, then everything "outside the split"
    — an excluded group's or excluded envelope's rows, plus the spend no envelope owns as one
    row — so the month's spend adds up to what was actually spent, split or not."""
    pairs = [pair(row) for group in vm.groups for row in group.rows]
    for p in pairs:
        p["spent"] = max(0.0, p["spent"])
    unmapped = vm.unmapped
    if unmapped is None:
        return pairs
    outside = [pair(row) for row in unmapped.rows]
    for p in outside:
        p["spent"] = max(0.0, p["spent"])
    pairs.extend(outside)
    loose = round((unmapped.amount_base or 0.0) - sum(p["spent"] for p in outside), 2)
    if loose > _SPEND_EPSILON:
        pairs.append(
            {
                "key": "__no_envelope__",
                "label": "Not in an envelope",
                "color": "",
                "assigned": 0.0,
                "spent": loose,
            }
        )
    return pairs


def _engine_total_budget(vm: ViewModel) -> float:
    """The engine's own idea of "how much is budgeted, total" — for zero-based this is Σ every
    group's "assigned" subtotal, which equals `budget_month()`'s own `totals.assigned` exactly
    (every envelope's assigned value is counted in exactly one group's subtotal, and the
    synthetic `__unsorted__`/`__unbudgeted__` groups always subtotal to 0 assigned). Envelope
    sums "funded" the same way. Any engine with no per-category money target (50/30/20) has no
    such figure — 0.0, not a guess."""
    key = _PROGRESS_MEASURE_KEYS.get(vm.engine_id, (None, None))[0]
    if key is None:
        return 0.0
    if vm.engine_id == "proportional":
        # Caps live on the rows (a group's subtotal is a share of income, not money).
        rows = [r for g in vm.groups for r in g.rows] + list(
            vm.unmapped.rows if vm.unmapped else []
        )
        return sum(_measure_value(r.measures, key) for r in rows)
    return sum(m.amount_base or 0.0 for g in vm.groups for m in g.subtotal if m.key == key)


def envelopes_view(vm: ViewModel) -> dict:
    """Assigned-vs-spent progress per row, plus the month totals both bars need — sourced from
    the home engine's `ViewModel` (RFC Decision G) rather than `budget_month()` directly.
    Byte-identical to the pre-Phase-4 output when zero-based is home (every existing book) —
    `tests/test_planning_summary.py` pins it."""
    rows = []
    for pair in engine_progress_rows(vm):
        assigned = pair["assigned"]
        spent = pair["spent"]
        if spent <= 0 and assigned <= 0:
            continue
        rows.append(
            {
                "key": pair["key"],
                "label": pair["label"],
                "color": pair["color"],
                "assigned": round(assigned, 2),
                "spent": round(spent, 2),
                # Bar width, capped — a 140%-spent envelope still draws a full bar.
                "fill_pct": round(min(100.0, (spent / assigned) * 100), 2)
                if assigned > 0
                else (100.0 if spent > 0 else 0.0),
                "over": spent > assigned if assigned > 0 else spent > 0,
            }
        )
    rows.sort(key=lambda r: r["spent"], reverse=True)
    budgeted = [r for r in rows if r["assigned"] >= _SPEND_EPSILON]
    total_spent = round(sum(r["spent"] for r in rows), 2)
    total_budget = round(_engine_total_budget(vm), 2)
    return {
        "rows": rows,
        "budgeted_rows": budgeted,
        "total_spent": total_spent,
        # The budgeted-only pair the "budget vs actual" widget shows in its header.
        "budgeted_spent": round(sum(r["spent"] for r in budgeted), 2),
        "budgeted_total": round(sum(r["assigned"] for r in budgeted), 2),
        "total_budget": total_budget,
        "pct_of_budget": round((total_spent / total_budget) * 100, 2) if total_budget > 0 else 0.0,
    }


def safe_to_spend_view(vm: ViewModel, table: dict, days_left: int) -> dict:
    """The "safe to spend" breakdown: what's left to assign, and what that is PER DAY over the
    days remaining in the month. Zero-based-home reads `budget_month()`'s own
    `ready_to_assign`/`totals.assigned` verbatim.

    The `inflows_to_assign` line went with the rest of the YNAB decomposition (ADR
    2026-09-09-ready-to-assign-follows-ynab-envelopes-are-the-category): Ready to Assign already
    reflects every inflow, and every spend that has LEFT cash with nothing holding it, so quoting
    the month's income beside it added nothing and invited the reading that safe-to-spend was
    income-driven. It is deliberately NOT every spend: an envelope HOLDS its own overspend until
    month-end, so a red row is not reflected here until the roll charges it. One number spread
    over the days left.

    No other engine has a Ready-to-Assign concept (50/30/20 has a savings RATE, not an
    unassigned-cash figure) — `headline` (the caller's own field) carries that engine's real
    hero number, so this degrades to zeros rather than mislabel a percent as money."""
    if vm.engine_id == "zero_based":
        ready_to_assign = round(float(table.get("ready_to_assign") or 0.0), 2)
        return {
            "ready_to_assign": ready_to_assign,
            "per_day": round(ready_to_assign / days_left, 2) if days_left > 0 else ready_to_assign,
            "assigned": round(float((table.get("totals") or {}).get("assigned") or 0.0), 2),
        }
    value = round(float(vm.headline.value or 0.0), 2) if vm.headline.unit == "money" else 0.0
    return {
        "ready_to_assign": value,
        "per_day": round(value / days_left, 2) if days_left > 0 else value,
        "assigned": 0.0,
    }


def home_spending_summary(
    *,
    month: int | None = None,
    year: int | None = None,
    base: str | None = None,
    txns: list[dict] | None = None,
    rates: dict | None = None,
) -> dict:
    """The month's expense-vs-budget summary, sourced from the book's HOME budget lens — the
    function `reports.py`'s `spending_summary` report calls now (RFC 2026-08-22-pluggable-budget-engines.md §8.1: before this, those
    two surfaces read `services.budgets.spending_summary` — the OTHER, unrelated
    per-category-LIMIT system — while the seven Overview widgets here read envelope/
    `budget_month` reality, so the two could disagree about the same month's numbers).

    Same shape as the former `budgets.spending_summary()` (`total_spent`/`total_available`/
    `pct_budget_used`/`daily_rate`/`projected_total`/`by_category`/`by_source`/...) so neither
    existing caller needed a render rewrite. `by_category`'s `available`/`status` now reflect
    the HOME ENGINE's own idea of "budget" (`engine_progress_rows`); 50/30/20 has no
    per-category money target, so that table degrades to empty rather than a forced number.
    `by_source` (spend by account) is genuinely paradigm-neutral, so it's read straight off the
    ledger — the same money math `services.budgets` used (`is_spending_expense` +
    `txn_base_amount`), independent of any engine."""
    today = user_today()
    m = month or today.month
    y = year or today.year
    base = base or base_currency()
    rates = rates if rates is not None else load_fx_cache()
    txns = txns if txns is not None else ledger_service.transactions.all()
    ym = f"{y:04d}-{m:02d}"

    vm = home_engine_view(ym)

    _, days_in_month = monthrange(y, m)
    days_elapsed = today.day if (y == today.year and m == today.month) else days_in_month

    category_summary = []
    total_available = 0.0
    for pair in engine_progress_rows(vm):
        spent = round(pair["spent"], 2)
        available = round(pair["assigned"], 2)
        total_available += available
        if available > 0:
            pct = spent / available * 100
            status = "over_budget" if pct > 100 else "on_track"
        else:
            pct, status = None, "unbudgeted"
        category_summary.append(
            {
                "category": pair["label"] or pair["key"] or "Uncategorized",
                "is_expense": True,
                "spent": spent,
                "limit": available,
                "rollover": False,
                "carryover": 0.0,
                "available": available,
                "budget": available,  # legacy alias: pre-BU1 callers/tests read "budget" (= available)
                "pct_used": round(pct, 1) if pct is not None else None,
                "status": status,
            }
        )
    category_summary.sort(key=lambda c: c["spent"], reverse=True)
    total_available = round(total_available, 2)

    month_txns = [
        t
        for t in txns
        if not t.get("deleted")
        and budgets_service.is_spending_expense(t)
        and (t.get("date") or "").startswith(ym)
    ]
    total_spent = round(sum(ledger_service.txn_base_amount(t, base, rates) for t in month_txns), 2)
    daily_rate = total_spent / days_elapsed if days_elapsed > 0 else 0

    by_src: dict[str, float] = {}
    for t in month_txns:
        sid = t.get("from_account_id") or "__none__"
        by_src[sid] = by_src.get(sid, 0.0) + ledger_service.txn_base_amount(t, base, rates)
    acct_map = {a["id"]: a for a in ledger_service.accounts.query_accounts(include_archived=True)}
    source_summary = sorted(
        [
            {
                "id": sid,
                "name": acct_map[sid]["name"] if sid in acct_map else "Unknown",
                "type": acct_map[sid].get("type") if sid in acct_map else None,
                "color": acct_map[sid].get("color") if sid in acct_map else None,
                "spent": round(spent, 2),
                "count": sum(
                    1 for t in month_txns if (t.get("from_account_id") or "__none__") == sid
                ),
            }
            for sid, spent in by_src.items()
        ],
        key=lambda x: x["spent"],
        reverse=True,
    )

    if total_available > 0:
        spend_frac = total_spent / total_available
        month_frac = days_elapsed / days_in_month
        burn_status = (
            "over_budget"
            if spend_frac > 1.0
            else "over_pace"
            if month_frac > 0 and spend_frac / month_frac > 1.1
            else "on_track"
        )
    else:
        burn_status = "unbudgeted"

    return {
        "month": m,
        "year": y,
        "days_in_month": days_in_month,
        "days_elapsed": days_elapsed,
        "total_spent": total_spent,
        "total_budget": total_available,
        "total_available": total_available,
        "pct_budget_used": round(total_spent / total_available * 100, 1)
        if total_available > 0
        else None,
        "burn_status": burn_status,
        "daily_rate": round(daily_rate, 2),
        "projected_total": round(daily_rate * days_in_month, 2),
        "by_category": category_summary,
        "by_source": source_summary,
        "transaction_count": len(month_txns),
        "base_currency": base,
        "engine_id": vm.engine_id,
    }
