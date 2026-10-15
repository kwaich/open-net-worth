"""
REST API for financial commitments (Phase 1.2).

Thin routes over CommitmentsStore — all storage logic lives in the store, all
funding math (coverage verdict, reconciliation) will live in commitments_service
(Phase 2; the `coverage` endpoint is intentionally absent until then).

A new CommitmentsStore() is constructed per request: the store is stateless (reads
disk on every call), so this carries no cost and keeps the path test-monkeypatchable.

    GET    /api/commitments                                  list (optional ?kind= &status=)
    POST   /api/commitments                                  create
    GET    /api/commitments/{id}                             single
    PUT    /api/commitments/{id}                             patch top-level fields
    DELETE /api/commitments/{id}                             delete
    PUT    /api/commitments/{id}/installments                replace installments[]
    PATCH  /api/commitments/{id}/installments/{inst_id}      patch ONE installment row
    PUT    /api/commitments/{id}/funding                     replace funding_plan[]
    PATCH  /api/commitments/{id}/funding/{funding_id}        patch ONE funding source
    PUT    /api/commitments/{id}/actions                     replace actions[]
    PATCH  /api/commitments/{id}/actions/{action_id}         patch ONE action row
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.core.errors import ValidationError
from backend.db.commitments_store import CommitmentsStore
from backend.services.commitments import COVERAGE_HORIZON_MONTHS

router = APIRouter()

# Registered before the dynamic route below so the literal path wins (same reason
# /api/accounts/tracking-health sits above /api/accounts/{account_id}).


@router.get("/api/commitments/summary")
def get_commitments_summary(days: int = 120):
    """The upcoming-commitments feed, the total going out within the next 30 days, and the
    coverage verdict — the three things a dashboard shows together."""
    from backend.services import commitments_summary as commitments_summary_service

    return commitments_summary_service.commitments_overview(days=days)


def _store() -> CommitmentsStore:
    return CommitmentsStore()


# ── Bodies ─────────────────────────────────────────────────────────────────────


class CommitmentCreate(BaseModel):
    name: str
    kind: str = "purchase"
    currency: str = ""
    status: str = "active"
    linked_position_id: Optional[str] = None
    linked_account_id: Optional[str] = None
    note: str = ""
    installments: list[dict] = []
    funding_plan: list[dict] = []


class CommitmentUpdate(BaseModel):
    changes: dict


class InstallmentsBody(BaseModel):
    installments: list[dict]


class InstallmentPatch(BaseModel):
    changes: dict


class FundingBody(BaseModel):
    funding_plan: list[dict]


class FundingSourcePatch(BaseModel):
    changes: dict


class ActionsBody(BaseModel):
    actions: list[dict]


class ActionPatch(BaseModel):
    changes: dict


# ── Collection ───────────────────────────────────────────────────────────────


@router.get("/api/commitments")
def list_commitments(
    kind: Optional[str] = None, status: Optional[str] = None, coverage: bool = True
):
    rows = _store().query_commitments(kind=kind, status=status)
    # Attach the per-commitment funding verdict (best-effort — never fail the list on it).
    if coverage and rows:
        try:
            from backend.services import commitments as commitments_service

            cov = commitments_service.coverage()
            by_id = {c["id"]: c for c in cov["commitments"]}
            for r in rows:
                v = by_id.get(r["id"])
                if v:
                    r["coverage"] = {
                        "assessed": v["assessed"],
                        "at_risk": v["at_risk"],
                        "shortfall_base": v["shortfall_base"],
                        "earliest_shortfall_date": v["earliest_shortfall_date"],
                    }
        except Exception:
            pass
    return rows


@router.get("/api/commitments/coverage")
def get_coverage(months: int = COVERAGE_HORIZON_MONTHS, floor: Optional[float] = None):
    """Portfolio-wide funding verdict (for Overview + the funding-outlook masthead): earliest
    shortfall, total gap, over-committed cash, per-commitment risk, and the monthly cushion
    `timeline` the chart plots. `floor` overrides the profile's cash-floor threshold for this
    call only (the masthead's horizon/floor controls) — omit to use the stored default."""
    from backend.services import commitments as commitments_service

    return commitments_service.coverage(months=max(1, min(months, 60)), floor=floor)


@router.get("/api/commitments/upcoming")
def get_upcoming(days: int = 60):
    """Date-sorted feed of upcoming (and overdue) installments, actions, and funding
    draws across all active commitments — for the Overview 'Upcoming' panel."""
    from backend.services import commitments as commitments_service

    return commitments_service.upcoming_events(days=max(1, min(days, 365)))


@router.get("/api/commitments/unmarked-payments")
def get_unmarked_payments():
    """Ledger transactions that look like an unpaid installment of a projected
    (purchase/loan_repayment) commitment — surfaced so the user can confirm the
    link instead of being asked to re-record a payment that already exists."""
    from backend.services import commitments as commitments_service

    return commitments_service.detect_unmarked_payments()


@router.post("/api/commitments")
def create_commitment(body: CommitmentCreate):
    return {"ok": True, "commitment": _store().create(body.model_dump())}


# ── Single ───────────────────────────────────────────────────────────────────


@router.get("/api/commitments/{commitment_id}")
def get_commitment(commitment_id: str):
    c = _store().find_by_id(commitment_id)
    if c is None:
        raise HTTPException(404, "Commitment not found")
    return c


@router.put("/api/commitments/{commitment_id}")
def update_commitment(commitment_id: str, body: CommitmentUpdate):
    # ``envelope_id`` is a sync-writer-only FK (planning.sync_commitment_envelopes / the one-shot
    # migration) — NEVER settable via the generic REST PUT. Clearing it here would make the next
    # sync see no FK and mint a DUPLICATE envelope for the still-active commitment.
    changes = {k: v for k, v in (body.changes or {}).items() if k != "envelope_id"}
    c = _store().update_commitment(commitment_id, changes)
    if c is None:
        raise HTTPException(404, "Commitment not found")
    return {"ok": True, "commitment": c}


@router.delete("/api/commitments/{commitment_id}")
def delete_commitment(commitment_id: str):
    store = _store()
    commitment = store.find_by_id(commitment_id)
    if commitment is None or not store.delete(commitment_id):
        raise HTTPException(404, "Commitment not found")
    # Retire the synced budget envelope too. sync_commitment_envelopes only reconciles envelopes
    # of commitments that STILL exist, so a hard-deleted commitment would otherwise ORPHAN its
    # envelope forever — a live, unarchived Commitments-group row nobody owns.
    env_id = commitment.get("envelope_id")
    if env_id:
        from backend.services import envelopes as envelopes_service
        from backend.services import planning as planning_service

        envelopes_service.force_delete_commitment_envelope(env_id)
        targets = planning_service.read_category_targets()
        if targets.pop(env_id, None) is not None:
            planning_service.write_category_targets(targets)
    return {"ok": True}


# ── Installments ─────────────────────────────────────────────────────────────


@router.put("/api/commitments/{commitment_id}/installments")
def set_installments(commitment_id: str, body: InstallmentsBody):
    from backend.services import commitments as commitments_service

    try:
        c = commitments_service.set_installments(commitment_id, body.installments)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if c is None:
        raise HTTPException(404, "Commitment not found")
    return {"ok": True, "commitment": c}


@router.patch("/api/commitments/{commitment_id}/installments/{installment_id}")
def patch_installment(commitment_id: str, installment_id: str, body: InstallmentPatch):
    # Routed through the service (not the store) — linking paid_txn_id here is the mark-paid
    # path the frontend uses, and the service's update_installment carries the auto-tag hook
    # (Mission #3: stamp the commitment's synced budget category onto the linked txn).
    from backend.services import commitments as commitments_service

    try:
        c = commitments_service.update_installment(commitment_id, installment_id, body.changes)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if c is None:
        raise HTTPException(404, "Commitment or installment not found")
    return {"ok": True, "commitment": c}


# ── Funding plan ─────────────────────────────────────────────────────────────


@router.put("/api/commitments/{commitment_id}/funding")
def set_funding_plan(commitment_id: str, body: FundingBody):
    c = _store().set_funding_plan(commitment_id, body.funding_plan)
    if c is None:
        raise HTTPException(404, "Commitment not found")
    return {"ok": True, "commitment": c}


@router.patch("/api/commitments/{commitment_id}/funding/{funding_id}")
def patch_funding_source(commitment_id: str, funding_id: str, body: FundingSourcePatch):
    """Patch ONE funding source — e.g. `{"disabled": true}` when the action that would have
    unlocked it is marked "not happening", or `{"disabled": false}` to undo that."""
    try:
        c = _store().update_funding_source(commitment_id, funding_id, body.changes)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if c is None:
        raise HTTPException(404, "Commitment or funding source not found")
    return {"ok": True, "commitment": c}


# ── Actions / events ─────────────────────────────────────────────────────────


@router.put("/api/commitments/{commitment_id}/actions")
def set_actions(commitment_id: str, body: ActionsBody):
    c = _store().set_actions(commitment_id, body.actions)
    if c is None:
        raise HTTPException(404, "Commitment not found")
    return {"ok": True, "commitment": c}


@router.patch("/api/commitments/{commitment_id}/actions/{action_id}")
def patch_action(commitment_id: str, action_id: str, body: ActionPatch):
    c = _store().update_action(commitment_id, action_id, body.changes)
    if c is None:
        raise HTTPException(404, "Commitment or action not found")
    return {"ok": True, "commitment": c}
