"""
budgets_service — budget / rollover-carryover / spending-summary money math (P2-BU1).

All budget arithmetic lives here (the services tier), not in ``api/ledger.py``. A category
budget moved from a bare number (``"food": 800``) to an object
(``"food": {"limit": 800, "rollover": false}``); ``normalize_budgets`` accepts both shapes
(legacy bare number / null / object) so reads are back-compatible without an eager rewrite.

Rollover (carryover): when a category has ``rollover: true``, prior months' unspent (or
overspent) rolls forward — ``available = limit + carryover``, where

    carryover(month) = Σ over months m in [rollover_start, month) of (available_m − spent_m)

This is a recursive walk (each month's available feeds the next), bounded by ``rollover_start``
so we never fold infinite / pre-adoption history. Carryover is ALWAYS derived (never stored):
turning rollover off, or moving ``rollover_start`` later, simply re-bases the fold on the next
read. Spend is valued in the CURRENT base via ``ledger.txn_base_amount`` (recomputed from the
native leg), so a base-currency change re-values prior months — no stale ``base_amount`` leaks.

Pure functions take injected ``txns`` / ``rates`` so the carryover math is unit-testable
without disk (mirrors ``tests/test_char_ledger_math``).

See .claude/rfcs/phase2/bu1-budgets-targets.md.
"""

from __future__ import annotations

from calendar import monthrange

from sqlalchemy import Connection

from backend.core.errors import ValidationError
from backend.db.profile_store import ProfileStore
from backend.models.category_defs import BUILTIN_CATEGORIES

# Wave 5 (JSON->DB): profile persistence moved to the DB-backed ProfileStore. The
# read-modify-write that budgets.py's lock used to serialise (a budget key-write racing a
# full-profile PUT) now runs inside ONE DB transaction (ProfileStore.set_key / set_raw), so the
# guarantee holds even across uvicorn workers — a process-local threading.Lock never could.
_profile_store = ProfileStore()
from backend.core.user_time import user_today
from backend.services import ledger as svc
from backend.services.fx_cache import load_fx_cache

# Spending categories that can carry a budget — DERIVED from each built-in's ``kind`` (no
# hand-maintained key list to drift; ``tax`` fell out of budgeting exactly because the old list
# was hand-kept). models.category_defs is a leaf, so importing it here creates no cycle. Owned
# in the services tier so budget math never up-imports api/ledger; api/ledger re-exports this set.
EXPENSE_CATEGORIES = frozenset(c["id"] for c in BUILTIN_CATEGORIES if c["kind"] == "expense")


def is_spending_expense(txn: dict) -> bool:
    """True iff ``txn`` is a real cash-outflow expense — i.e. it counts toward spending totals,
    the cash-flow baseline, and the forecast scorecard.

    A row qualifies when ``kind == "expense"``. Investment cash legs now carry ``kind="investment"``
    (not kind=expense + category=investment), so they are naturally excluded here. ``split`` and
    ``uncategorized`` expenses DO count — the money still left the account.
    Income / transfer / adjustment / investment rows are not expenses and return ``False``.
    """
    return txn.get("kind") == "expense"


# ── Profile-backed budget I/O ───────────────────────────────────────────────────


def _read_profile_raw() -> dict:
    return _profile_store.get_raw()


def _write_profile_key(key: str, value, *, conn: Connection | None = None) -> None:
    """Atomic single-key update — the read-modify-write is ONE DB transaction (ProfileStore
    enforces it), so a concurrent key write or a full-profile PUT can no longer lost-update it.
    ``conn`` composes it into a caller's open transaction instead."""
    _profile_store.set_key(key, value, conn=conn)


def write_profile_raw(profile: dict) -> None:
    """Persist the whole profile dict in one transaction (checkpoint-covered via the DB write hook)."""
    _profile_store.set_raw(profile)


def normalize_budgets(raw) -> dict:
    """Project the stored ``monthly_budgets`` (bare number / null / object) → canonical
    ``{category: {limit: float, rollover: bool}}`` (AC-1). A bare number becomes
    ``{limit: n, rollover: false}``; null/absent → ``{}``. Non-positive limits are dropped."""
    if not raw or not isinstance(raw, dict):
        return {}
    out: dict[str, dict] = {}
    for cat, val in raw.items():
        if isinstance(val, dict):
            limit = float(val.get("limit") or 0.0)
            rollover = bool(val.get("rollover", False))
        else:
            try:
                limit = float(val)
            except (TypeError, ValueError):
                continue
            rollover = False
        if limit <= 0:
            continue
        out[cat] = {"limit": round(limit, 2), "rollover": rollover}
    return out


def read_budgets() -> dict:
    """Return the normalized ``{category: {limit, rollover}}`` map (no eager rewrite)."""
    return normalize_budgets(_read_profile_raw().get("monthly_budgets"))


def write_budgets(budgets: dict) -> None:
    """Persist canonically (normalized object shape) onto profile.json."""
    _write_profile_key("monthly_budgets", normalize_budgets(budgets))


def expense_categories(budgets: dict | None = None) -> set[str]:
    """Categories that can carry a budget = expense-kind category set ∪ any stored-budget category.

    Store-first: reads expense-kind categories from CategoryStore (the single source of truth
    since the categories-as-data re-arch — every category, custom or built-in, lives there from
    the moment it's created). Falls back to the built-in ``EXPENSE_CATEGORIES`` list only when
    the store is entirely empty (test env, pre-seed).

    The stored-budget union is a backstop so a budgetable category is never gated out by its
    own configured limit.

    Note: ``investment`` is now a transaction KIND, not a category, so it is never in this set —
    no subtraction is needed (the old NON_SPENDING_EXPENSE_CATEGORIES guard is gone)."""
    from backend.db.category_store import CategoryStore

    store = CategoryStore()
    rows = store.all()
    if rows:
        cats: set[str] = {r["id"] for r in rows if r.get("kind") == "expense"}
    else:
        cats = set(EXPENSE_CATEGORIES)
    if budgets:
        cats |= set(budgets.keys())
    return cats


def read_budget_config() -> dict:
    """Return ``{rollover_start, budget_start, pre_budget_baseline}``.

    ``budget_start`` (balance-based RTA) is the month the plan begins assigning from balances;
    it defaults to ``rollover_start`` (the carry-walk floor doubles as the RTA baseline anchor).
    ``pre_budget_baseline`` is the captured no-jump continuity constant (base ccy; None until a
    cutover capture runs) — see ``.claude/rfcs/balance-based-rta.md`` §3."""
    cfg = _read_profile_raw().get("budget_config") or {}
    rollover_start = cfg.get("rollover_start") or None
    return {
        "rollover_start": rollover_start,
        "budget_start": cfg.get("budget_start") or rollover_start,
        "pre_budget_baseline": cfg.get("pre_budget_baseline"),
    }


def _validate_anchor_month(field: str, value: str) -> str:
    """A carry/RTA anchor must be a real YYYY-MM at or before the current month.

    Enforced HERE rather than at each caller: this function is the single writer of
    ``budget_config``, and the callers (the REST route via ``planning.set_rollover_start``, the
    ``set_spending_limit_rollover_start`` AI tool) had drifted — the tool's own check accepted
    ``2026-99`` and any future month at all. A FUTURE anchor is not merely odd: it is also
    ``planning._ready_to_assign``'s zero-line, so every month before it — i.e. every month the
    user can actually reach — renders Ready to Assign and every envelope as 0."""
    today = user_today()
    if not isinstance(value, str) or len(value) != 7 or value[4] != "-":
        raise ValidationError(f"{field} must be YYYY-MM, got {value!r}")
    try:
        year, month = int(value[:4]), int(value[5:7])
    except ValueError:
        raise ValidationError(f"{field} must be YYYY-MM, got {value!r}") from None
    if not 1 <= month <= 12:
        raise ValidationError(f"{field} month out of range: {value!r}")
    if (year, month) > (today.year, today.month):
        raise ValidationError(
            f"{field} cannot be in the future: {value} is after {_ym(today.year, today.month)}"
        )
    return value


def write_budget_config(config: dict, *, conn: Connection | None = None) -> None:
    """Persist the budget config, MERGING onto any existing keys (a caller that sets only
    ``rollover_start`` must not wipe a captured ``pre_budget_baseline``, and vice-versa).

    The SINGLE writer of ``budget_config``, and therefore where the anchor months are validated
    (``_validate_anchor_month``) — REST, the AI tool and any future caller all inherit the same
    refusal. Clearing (``None``/``""``) is always allowed. ``conn`` composes the write into a
    caller's open transaction (``planning.fresh_start``, which moves the floor and clears the
    month's assignments as ONE unit)."""
    config = config or {}
    merged = dict(_read_profile_raw().get("budget_config") or {})
    if "rollover_start" in config:
        start = config.get("rollover_start") or None
        merged["rollover_start"] = (
            None if start is None else _validate_anchor_month("rollover_start", start)
        )
    if "budget_start" in config:
        start = config.get("budget_start") or None
        merged["budget_start"] = (
            None if start is None else _validate_anchor_month("budget_start", start)
        )
    if "pre_budget_baseline" in config:
        pbb = config.get("pre_budget_baseline")
        merged["pre_budget_baseline"] = None if pbb is None else round(float(pbb), 2)
    _write_profile_key("budget_config", merged, conn=conn)


# ── Month helpers ────────────────────────────────────────────────────────────────


def _ym(year: int, month: int) -> str:
    return f"{year:04d}-{month:02d}"


def _prev_month(year: int, month: int) -> tuple[int, int]:
    return (year - 1, 12) if month == 1 else (year, month - 1)


def _months_from(start_ym: str, year: int, month: int) -> list[tuple[int, int]]:
    """Inclusive list of (year, month) tuples from ``start_ym`` (YYYY-MM) up to (year, month).
    Empty when ``start_ym`` is after the target month or malformed."""
    try:
        sy, sm = int(start_ym[:4]), int(start_ym[5:7])
    except (ValueError, IndexError):
        return []
    if (sy, sm) > (year, month):
        return []
    out: list[tuple[int, int]] = []
    cy, cm = sy, sm
    # guard against a pathological / far-past start so the walk is always bounded
    while (cy, cm) <= (year, month) and len(out) < 1200:
        out.append((cy, cm))
        cy, cm = (cy + 1, 1) if cm == 12 else (cy, cm + 1)
    return out


# ── Spend folding ────────────────────────────────────────────────────────────────


def _spent_by_category(txns: list[dict], year: int, month: int, base: str, rates: dict) -> dict:
    """Σ expense magnitude per category for one month, valued in the CURRENT base.

    A SPLIT txn (P3-SP) distributes each line to its OWN category via
    ``txn_split_base_amount`` (a pure fraction of the leg base, so the lines sum EXACTLY to the
    leg total — the month grand total is unchanged whether or not a txn is split). A non-split
    txn folds its single (category, leg) exactly as before."""
    prefix = _ym(year, month)
    out: dict[str, float] = {}
    for t in txns:
        if t.get("deleted") or t.get("kind") != "expense":
            continue
        if not (t.get("date") or "").startswith(prefix):
            continue
        splits = t.get("splits")
        if splits:
            for s in splits:
                out[s["category"]] = out.get(s["category"], 0.0) + svc.txn_split_base_amount(
                    t, s, base, rates
                )
        else:
            cat = t.get("category") or "other"
            out[cat] = out.get(cat, 0.0) + svc.txn_base_amount(t, base, rates)
    return out


def carryover_for(
    category: str,
    year: int,
    month: int,
    *,
    budgets: dict,
    config: dict,
    txns: list[dict],
    base: str,
    rates: dict,
) -> float:
    """Carryover into (year, month) for ``category`` = Σ over months m in
    [rollover_start, month) of (available_m − spent_m), where ``available_m = limit + (carry
    accumulated through m-1)``. Each month's surplus/deficit is added to the running carry, so
    the RFC AC-3 example holds: spend 600 then 700 against an 800 cap from 2026-04 →
    (800−600) + (1000−700) = 500 by June. 0 when the category has no rollover (AC-2), no
    ``rollover_start`` set, or the start is on/after the target month. Bounded by
    ``rollover_start`` (AC-5); derived, never stored (AC-6); base-currency via
    ``txn_base_amount`` (AC-7)."""
    cb = budgets.get(category) or {}
    if not cb.get("rollover"):
        return 0.0
    start = (config or {}).get("rollover_start")
    if not start:
        return 0.0
    limit = float(cb.get("limit") or 0.0)
    # Months strictly before the target month, from rollover_start.
    py, pm = _prev_month(year, month)
    prior_months = _months_from(start, py, pm)
    # carry into (year, month) = Σ over prior months of (available_m − spent_m), where
    # available_m = limit + (carry accumulated so far) — each month's surplus/deficit is
    # ADDED to the running carry (per the RFC AC-3 example: (800−600)+(1000−700)=500).
    carry = 0.0
    for my, mm in prior_months:
        spent = _spent_by_category(txns, my, mm, base, rates).get(category, 0.0)
        available = limit + carry
        carry += available - spent
    return round(carry, 2)


# ── Spending summary (expenses only; transfers + adjustments excluded) ──────────


def spending_summary(
    month: int | None = None,
    year: int | None = None,
    *,
    txns: list[dict] | None = None,
    base: str | None = None,
    rates: dict | None = None,
) -> dict:
    """Expense-only spending summary for (month, year): total spent vs available, burn rate,
    per-category breakdown (now incl. ``limit``/``carryover``/``available``), and by-source.

    With rollover off everywhere, ``available == limit`` and ``carryover == 0`` for every
    category, so the totals/verdicts match the legacy summary (AC-2, regression AC-16). Inputs
    (txns/base/rates) are injectable for unit tests; otherwise read live."""
    today = user_today()
    m = month or today.month
    y = year or today.year

    base = base or svc.base_currency()
    if rates is None:
        rates = load_fx_cache()  # resolve once so the carryover walk doesn't reload per call
    if txns is None:
        txns = svc.transactions.all()

    budgets = read_budgets()
    config = read_budget_config()
    expense_cats = expense_categories(budgets)

    prefix = _ym(y, m)
    # is_spending_expense excludes the investment leg (a security buy isn't spending) so the
    # by-account fold below matches the by-category view, which already flags it non_expense.
    month_txns = [
        t
        for t in txns
        if not t.get("deleted")
        and is_spending_expense(t)
        and (t.get("date") or "").startswith(prefix)
    ]

    _, days_in_month = monthrange(y, m)
    days_elapsed = today.day if (y == today.year and m == today.month) else days_in_month

    def _spent(t: dict) -> float:
        return svc.txn_base_amount(t, base, rates)

    # By category — via the shared (split-aware, P3-SP) fold so a split txn lands in each of
    # its line categories, not under the inert SPLIT sentinel. Identical to the per-txn loop for
    # non-split data; the month grand total is unaffected (split lines sum to the leg total).
    by_cat = _spent_by_category(txns, y, m, base, rates)
    for cat in budgets:
        by_cat.setdefault(cat, 0.0)

    category_summary = []
    total_available = 0.0
    for cat, spent in sorted(by_cat.items()):
        is_expense = cat in expense_cats
        cb = budgets.get(cat) or {}
        limit = float(cb.get("limit") or 0.0) if is_expense else 0.0
        rollover = bool(cb.get("rollover")) if is_expense else False
        carryover = (
            carryover_for(
                cat, y, m, budgets=budgets, config=config, txns=txns, base=base, rates=rates
            )
            if rollover
            else 0.0
        )
        available = round(limit + carryover, 2)
        total_available += available
        if is_expense and available > 0:
            pct = spent / available * 100
            if pct > 100:
                status = "over_budget"
            elif days_elapsed > 0 and (spent / days_elapsed) > (available / days_in_month) * 1.05:
                status = "over_pace"
            else:
                status = "on_track"
        elif is_expense:
            status, pct = "unbudgeted", None
        else:
            status, pct = "non_expense", None
        category_summary.append(
            {
                "category": cat,
                "is_expense": is_expense,
                "spent": round(spent, 2),
                "limit": round(limit, 2),
                "rollover": rollover,
                "carryover": carryover,
                "available": available,
                # legacy alias: pre-BU1 callers/tests read "budget" (= available)
                "budget": available,
                "pct_used": round(pct, 1) if pct is not None else None,
                "status": status,
            }
        )

    # By account (from-leg of each expense)
    by_src: dict[str, float] = {}
    for t in month_txns:
        sid = t.get("from_account_id") or "__none__"
        by_src[sid] = by_src.get(sid, 0.0) + _spent(t)

    acct_map = {a["id"]: a for a in svc.accounts.query_accounts(include_archived=True)}
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

    total_spent = sum(_spent(t) for t in month_txns)
    daily_rate = total_spent / days_elapsed if days_elapsed > 0 else 0

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
        "total_spent": round(total_spent, 2),
        # legacy key "total_budget" kept; now reflects total AVAILABLE (= limit+carryover)
        "total_budget": round(total_available, 2),
        "total_available": round(total_available, 2),
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
        "rollover_start": config.get("rollover_start"),
    }
