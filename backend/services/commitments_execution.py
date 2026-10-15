"""Plan vs. reality for one commitment: what was due by today and has not happened.

A funding plan is a list of INTENTIONS (set aside 7,653 in August, draw the loan on 15 Sep).
Read raw, a past-dated `planned` row looks exactly like money that exists — and a model
reading it told the owner their savings were "on track" when two set-asides had never been
made (live incident 2026-10-13). Every flag here is DERIVED from the clock, never stored,
for the same reason `commitments.funding_source_overdue` is.
"""

from __future__ import annotations

from backend.core.user_time import user_today
from backend.services import commitments as commitments_service
from backend.services import ledger as svc

MAX_LISTED = 12


def _action_overdue(action: dict, today: str) -> bool:
    # Only a pending action can be behind: done and "missed" (not happening) are closed, the
    # same rule `commitments.upcoming_events` applies.
    if action.get("status", "pending") != "pending":
        return False
    due = action.get("due_date")
    return bool(due) and str(due)[:10] < today


def _installment_overdue(inst: dict, today: str) -> bool:
    if commitments_service.is_paid(inst):
        return False
    due = inst.get("due_date")
    return bool(due) and str(due)[:10] < today


def _brief(row: dict, date_key: str, default_ccy: str | None = None) -> dict:
    out = {"id": row.get("id"), "label": row.get("label"), date_key: row.get(date_key)}
    if row.get("amount") not in (None, ""):
        out["amount"] = row.get("amount")
        out["currency"] = row.get("currency") or default_ccy
    return out


def plan_execution(commitment: dict, base: str, rates: dict) -> dict | None:
    """Past-due funding rows, actions and installments, or None when nothing is behind."""
    today = user_today().isoformat()
    funding = [
        s
        for s in commitment.get("funding_plan") or []
        if commitments_service.funding_source_overdue(s, today)
    ]
    actions = [a for a in commitment.get("actions") or [] if _action_overdue(a, today)]
    installments = [
        i for i in commitment.get("installments") or [] if _installment_overdue(i, today)
    ]
    if not (funding or actions or installments):
        return None
    out: dict = {"as_of": today}
    if funding:
        total = sum(commitments_service.live_source_value(s, base, rates) for s in funding)
        out["funding_not_realized"] = [_brief(s, "available_date") for s in funding[:MAX_LISTED]]
        out["funding_not_realized_count"] = len(funding)
        out["funding_not_realized_total_base"] = {"amount": round(total, 2), "currency": base}
    if actions:
        out["actions_overdue"] = [_brief(a, "due_date") for a in actions[:MAX_LISTED]]
        out["actions_overdue_count"] = len(actions)
    if installments:
        out["installments_unpaid_past_due"] = [
            _brief(i, "due_date", commitment.get("currency")) for i in installments[:MAX_LISTED]
        ]
        out["installments_unpaid_past_due_count"] = len(installments)
    out["warning"] = (
        "These rows' dates have passed and they are NOT marked done. A not-realized funding "
        "row is money the plan EXPECTED, not money that exists — never count it as banked or "
        "call the plan on track. Check the ledger (get_transactions / list_accounts) for "
        "what actually arrived, and tell the user which planned steps were missed."
    )
    return out


def annotate(commitment: dict, base: str | None = None, rates: dict | None = None) -> dict:
    """A copy of `commitment` with `plan_execution` added when anything is behind."""
    base = (base or svc.base_currency()).upper()
    if rates is None:
        from backend.services.fx_cache import load_fx_cache

        rates = load_fx_cache()
    status = plan_execution(commitment, base, rates)
    return {**commitment, "plan_execution": status} if status else dict(commitment)
