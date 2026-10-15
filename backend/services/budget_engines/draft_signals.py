"""Shared trailing-spend-history + stability signal for every budget engine's ``draft()``
heuristic — zero-based's monthly-target sizing (``zero_based.py``) and the history behind
50/30/20's per-group proposal. Extracted so neither engine duplicates the other's
history-window math (RFC ``2026-08-22-pluggable-budget-engines.md``, Decision H's
"``draft()`` is load-bearing" applying to both).

Deterministic and dependency-free BY DESIGN: no LLM, no network — a ``draft()`` living in
``services/`` is import-pure (CLAUDE.md), an LLM call would need ``core.llm_seam`` and is
explicitly out of scope. Every amount ``build_history`` produces comes from
``ledger.txn_base_amount`` — never a caller's own FX, never a raw stored snapshot.
"""

from __future__ import annotations

from backend.core.user_time import user_today
from backend.services import ledger as ledger_service

# Coefficient-of-variation bands for the spend-stability signal: a recurring bill (rent,
# insurance) has LOW month-to-month variance; a discretionary category (dining, hobby buys)
# has HIGH variance. Picked as clearly-separated, round numbers — not fit to any dataset;
# the signal only breaks ties when a category's LABEL itself is ambiguous or unrecognized.
_STABLE_CV = 0.35
_VOLATILE_CV = 0.75


def current_period() -> str:
    t = user_today()
    return f"{t.year:04d}-{t.month:02d}"


def _prev_month(ym: str) -> str:
    y, m = int(ym[:4]), int(ym[5:7])
    return f"{y - 1:04d}-12" if m == 1 else f"{y:04d}-{m - 1:02d}"


def build_history(
    txns: list[dict], base: str, rates: dict, period: str, months: int = 6
) -> dict[str, list[float]]:
    """``{category_id: [monthly_expense_total, ...]}``, OLDEST TO NEWEST, over the
    ``months``-month trailing window ending at ``period`` (inclusive) — one list entry
    per month the category had ANY spend (a month with zero spend simply contributes
    nothing, which is exactly what ``spend_stability``'s coefficient-of-variation needs —
    order itself doesn't affect that calc, chronological is just the readable
    convention). Every leg valued via ``ledger.txn_base_amount`` — never a caller's own
    FX, never a raw stored snapshot."""
    window: list[str] = [period]
    for _ in range(months - 1):
        window.append(_prev_month(window[-1]))
    window_set = set(window)
    by_month_cat: dict[str, dict[str, float]] = {m: {} for m in window}
    for t in txns:
        if t.get("deleted") or t.get("kind") != "expense":
            continue
        month = (t.get("date") or "")[:7]
        if month not in window_set:
            continue
        # Split-aware, mirroring proportional_fold's own totals(): a SPLIT
        # expense's real categorization lives on t["splits"], never the top-level
        # category="split" placeholder.
        splits = t.get("splits")
        lines = (
            [
                (s.get("category"), ledger_service.txn_split_base_amount(t, s, base, rates))
                for s in splits
            ]
            if splits
            else [(t.get("category"), ledger_service.txn_base_amount(t, base, rates))]
        )
        for cat, amt in lines:
            if not cat:
                continue
            by_month_cat[month][cat] = by_month_cat[month].get(cat, 0.0) + amt
    history: dict[str, list[float]] = {}
    for month in reversed(window):
        for cat, amt in by_month_cat[month].items():
            history.setdefault(cat, []).append(amt)
    return history


def spend_stability(amounts: list[float]) -> str:
    """'stable' | 'volatile' | 'unknown' from the coefficient of variation of a category's
    active-month spend. 'unknown' with fewer than 2 active months — not enough signal."""
    if len(amounts) < 2:
        return "unknown"
    mean = sum(amounts) / len(amounts)
    if mean <= 0:
        return "unknown"
    variance = sum((a - mean) ** 2 for a in amounts) / len(amounts)
    cv = (variance**0.5) / mean
    if cv <= _STABLE_CV:
        return "stable"
    if cv >= _VOLATILE_CV:
        return "volatile"
    return "unknown"


def envelope_months(category_ids: list[str], hist: dict[str, list[float]]) -> list[float]:
    """An envelope's monthly spend series: its categories' series summed month-by-month.

    ``build_history`` emits one entry per month a category had ANY spend, so two categories in
    the same envelope can return different-length series. They are summed by INDEX from the
    newest end (the series are oldest-to-newest), which keeps the most recent months — the ones
    that actually inform a target — aligned with each other.

    Sizes an envelope from its own history — zero-based's monthly targets read it.
    """
    series = [hist.get(c) or [] for c in category_ids]
    series = [s for s in series if s]
    if not series:
        return []
    longest = max(len(s) for s in series)
    out = [0.0] * longest
    for s in series:
        offset = longest - len(s)
        for i, amount in enumerate(s):
            out[offset + i] += amount
    return [round(a, 2) for a in out]


def suggest_amount(monthly_amounts: list[float]) -> float:
    """A starting amount for one envelope, from its own trailing spend. ``0.0`` with no history
    — honestly "we don't know", not a guess dressed up as one.

    MEDIAN, not mean: one holiday month or a single annual insurance payment should not
    permanently inflate the figure the user lives inside every month. With an even number of
    months the lower-middle value is taken rather than the midpoint average, which keeps the
    result a figure that actually occurred.
    """
    amounts = sorted(a for a in monthly_amounts if a > 0)
    if not amounts:
        return 0.0
    return round(amounts[(len(amounts) - 1) // 2], 2)
