"""
targets_service — deadline-savings ("save X by date Y") math + auto-allocation (P2-BU1).

All target arithmetic lives here (the services tier); the store is dumb storage. Per target
the service computes, in base currency:

  - remaining           = max(0, target_amount − current_amount)
  - months_remaining    = full calendar months from today to target_date (≥0; 0 when today/past)
  - required_monthly     = remaining / months_remaining  (or the whole remaining when 0 months —
                          finite, never a divide-by-zero / inf — AC-10)
  - expected_by_now      = straight-line pace from created_at → target_date
  - status ∈ {completed, ahead, on_track, behind, past_due}  (pace verdict — AC-8/9/10)

current_amount is authoritative-when-linked: if linked_account_ids is non-empty, the SUMMED live
balance of those accounts (base currency) overrides the stored figure (computed-not-stored,
mirroring commitments' asset_sale live valuation). A missing/archived linked account is skipped.

allocation_summary is COMPUTE-ONLY (AC-13): it derives the suggested monthly earmark across
active targets and compares it to the liquid-cash baseline + monthly free cash. It performs NO
ledger write — actual contributions are normal transfers the user/Lucius confirms.

See .claude/rfcs/phase2/bu1-budgets-targets.md.
"""

from __future__ import annotations

from datetime import date

from backend.core.fx import to_base
from backend.core.user_time import user_today
from backend.db.targets_store import TargetsStore
from backend.services import cashflow
from backend.services import ledger as svc
from backend.services.fx_cache import load_fx_cache

# Tolerance band (fraction of target) for the on-pace verdict before flagging ahead/behind.
_PACE_TOLERANCE = 0.05

_TODAY: str | None = None  # test seam (set to an ISO date to pin "today")

targets_store = TargetsStore()


def _resolve_linked_accounts(data: dict) -> dict:
    """Resolve ``linked_account_ids`` (ids or exact names) to stored ids, RAISING
    ``ValidationError`` on an unknown or ambiguous ref. A dead id used to be stored as given
    and skipped on read, so the goal's progress silently fell back to ``current_amount``
    (2026-10-13 audit)."""
    refs = data.get("linked_account_ids")
    if not refs:
        return data
    from backend.services.accounts import all_account_rows, require_account_id

    refs = refs if isinstance(refs, (list, tuple)) else [refs]
    rows = all_account_rows()
    resolved = [
        require_account_id(ref, field="linked_account_ids", rows=rows)
        for ref in refs
        if ref is not None and str(ref).strip()
    ]
    return {**data, "linked_account_ids": resolved}


def create_target(data: dict) -> dict:
    """The goal write path: linked accounts resolved, then the store's own invariants."""
    return targets_store.create(_resolve_linked_accounts(data))


def update_target(target_id: str, changes: dict) -> dict | None:
    return targets_store.update_target(target_id, _resolve_linked_accounts(changes))


def _today() -> date:
    return date.fromisoformat(_TODAY) if _TODAY else user_today()


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except (ValueError, TypeError):
        return None


def _months_between(start: date, end: date) -> int:
    """Whole calendar months from start to end (>=0). Counts a month only once start's
    day-of-month has been reached/passed in the end month, so 6 full months out → 6."""
    if end <= start:
        return 0
    months = (end.year - start.year) * 12 + (end.month - start.month)
    if end.day < start.day:
        months -= 1
    return max(0, months)


def _read_profile() -> dict:
    from backend.services.profile import read_profile_raw

    return read_profile_raw()


def _linked_balance_base(account_ids: list[str], base: str, rates: dict) -> float | None:
    """Summed live balance of the linked accounts, converted to base (computed-not-stored).

    Returns None when no usable account is found (empty list, or every id missing/archived) so
    the caller falls back to the stored current_amount. A missing/archived account is skipped, not
    a 500: a goal funded by two HYSAs still reports the surviving one if the other is deleted."""
    total = 0.0
    found = False
    for account_id in account_ids or []:
        acct = svc.accounts.find_by_id(account_id)
        if not acct or acct.get("archived"):
            continue
        txns = svc.transactions.all()
        bal = svc.account_balance(acct, txns, svc.opening_date(acct, txns))
        total += svc.to_base(bal, acct.get("currency") or base, base, rates)
        found = True
    return total if found else None


def hydrate(target: dict, base: str, rates: dict) -> dict:
    """Return the target enriched with base-currency progress + pace verdict. Pure given
    the injected base/rates; reads the linked account balance live when linked."""
    currency = (target.get("currency") or base).upper()
    target_amount = float(target.get("target_amount") or 0.0)

    current_amount = float(target.get("current_amount") or 0.0)
    linked_ids = target.get("linked_account_ids") or []
    linked_current_base: float | None = None
    if linked_ids:
        linked_current_base = _linked_balance_base(linked_ids, base, rates)

    target_amount_base = to_base(target_amount, currency, base, rates)
    if linked_current_base is not None:
        current_base = linked_current_base
    else:
        current_base = to_base(current_amount, currency, base, rates)

    remaining_base = round(max(0.0, target_amount_base - current_base), 2)

    today = _today()
    target_date = _parse_date(target.get("target_date"))
    created = _parse_date(target.get("created_at")) or today

    months_remaining = _months_between(today, target_date) if target_date else 0
    if months_remaining > 0:
        required_monthly_base = round(remaining_base / months_remaining, 2)
    else:
        required_monthly_base = remaining_base  # whole shortfall, finite (AC-10)

    # ── Pace verdict ────────────────────────────────────────────────────────────
    # A goal is "completed"/Funded ONLY when it has a POSITIVE target AND the progress
    # actually meets it — never just because a stored status says so. A $0-target goal,
    # or one with no contributions, must not read "Funded". A user-declared "cancelled"
    # is an authored terminal state and passes through verbatim.
    declared = target.get("status")
    funded = target_amount_base > 0 and current_base >= target_amount_base
    if declared == "cancelled":
        status = "cancelled"
    elif funded:
        status = "completed"
    elif target_date and today >= target_date:
        status = "past_due"
    else:
        # Straight-line expectation: fraction of the created→target window elapsed.
        status = "on_track"
        if target_date and target_amount_base > 0:
            total_days = (target_date - created).days
            elapsed_days = (today - created).days
            # Only flag ahead/behind once meaningful time has elapsed. With zero elapsed time
            # (a freshly-created target, the common case) the straight-line expected_by_now is 0,
            # so any current_amount would read as "ahead" even when the target is wildly
            # underfunded vs its deadline — so stay on_track until the window has actually begun.
            if total_days > 0 and elapsed_days > 0:
                frac = min(1.0, max(0.0, elapsed_days / total_days))
                expected_by_now = target_amount_base * frac
                tol = target_amount_base * _PACE_TOLERANCE
                if current_base >= expected_by_now + tol:
                    status = "ahead"
                elif current_base < expected_by_now - tol:
                    status = "behind"

    return {
        **target,
        "currency": currency,
        "current_amount": round(current_amount, 2),
        "linked_balance_base": round(linked_current_base, 2)
        if linked_current_base is not None
        else None,
        "current_base": round(current_base, 2),
        "target_amount_base": round(target_amount_base, 2),
        "remaining_base": remaining_base,
        "months_remaining": months_remaining,
        "required_monthly_base": required_monthly_base,
        "status": status,
        "base_currency": base,
    }


def list_hydrated(status: str | None = None) -> list[dict]:
    """All targets, hydrated + sorted by priority then name. Reads FX once."""
    base = svc.base_currency()
    rates = load_fx_cache()
    rows = [hydrate(t, base, rates) for t in targets_store.query_targets(status=status)]
    rows.sort(key=lambda r: (r.get("priority", 1), (r.get("name") or "").lower()))
    return rows


def get_hydrated(target_id: str) -> dict | None:
    t = targets_store.find_by_id(target_id)
    if t is None:
        return None
    return hydrate(t, svc.base_currency(), load_fx_cache())


def allocation_summary() -> dict:
    """Compute-only auto-allocation (AC-13): total required monthly earmark across ACTIVE
    targets vs the liquid-cash baseline + monthly free cash from profile. NO ledger write."""
    base = svc.base_currency()
    rates = load_fx_cache()
    active = [
        hydrate(t, base, rates)
        for t in targets_store.query_targets()
        if (t.get("status") or "active") == "active"
    ]
    # exclude already-completed (remaining 0) from the earmark
    contributing = [r for r in active if r["remaining_base"] > 0]
    contributing.sort(key=lambda r: (r.get("priority", 1), (r.get("name") or "").lower()))
    total_required = round(sum(r["required_monthly_base"] for r in contributing), 2)

    profile = _read_profile()
    monthly_income = float(profile.get("monthly_net_income") or 0.0)
    monthly_expenses = float(profile.get("monthly_expenses") or 0.0)
    # Free cash after the month's bills — a cash-flow figure for goal funding, deliberately NOT
    # the retirement saving (services.fire.monthly_saving): what is left over is not what they
    # have decided to put aside.
    monthly_free = round(monthly_income - monthly_expenses, 2)

    liquid_base = cashflow.cash_balance_base(base)

    return {
        "base_currency": base,
        "targets": [
            {
                "id": r["id"],
                "name": r["name"],
                "required_monthly_base": r["required_monthly_base"],
                "remaining_base": r["remaining_base"],
                "months_remaining": r["months_remaining"],
                "priority": r.get("priority", 1),
                "status": r["status"],
            }
            for r in contributing
        ],
        "total_required_monthly_base": total_required,
        "monthly_free_base": monthly_free,
        "free_after_targets_base": round(monthly_free - total_required, 2),
        "liquid_baseline_base": round(liquid_base, 2),
        "shortfall_base": round(max(0.0, total_required - monthly_free), 2),
    }
