"""
REST API for savings targets (P2-BU1) — the budgeting-tab "save X by date Y" primitive.

Thin routes over TargetsStore + services.targets (all target math lives in the service).
A new store/service read happens per request (stateless, reads disk each call), keeping
the path test-monkeypatchable.

    GET    /api/targets                list (optional ?status=) — hydrated (progress + pace)
    POST   /api/targets                create
    GET    /api/targets/allocation     compute-only auto-allocation summary (no write)
    GET    /api/targets/{id}           single (hydrated) or 404
    PUT    /api/targets/{id}           patch (TargetUpdate)
    DELETE /api/targets/{id}           delete

The per-category rollover/limit REST endpoints that used to live in api/ledger.py
(``/api/spending/budgets``, ``/api/budget-config``, ``/api/spending/summary``) were retired
in RFC 2026-08-22-pluggable-budget-engines.md Phase 4 — zero frontend consumers. The
underlying ``services.budgets`` config (``read_budgets``/``write_budgets``/
``read_budget_config``/``write_budget_config``) is still live (``services.planning``'s
category universe, the category rename/merge hooks). Its AI setters
(``set_spending_limits``, ``set_spending_limit_rollovers``, ``set_spending_limit_rollover_start``)
were retired 2026-09-26 — no screen reads the flat per-category limits; the savings-goal tools
over these routes' store are ``ai/tools/goals.py``.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException

from backend.core.errors import ValidationError
from backend.models.target import TargetCreate, TargetUpdate
from backend.services import targets as targets_service

router = APIRouter()


@router.get("/api/targets")
def list_targets(status: Optional[str] = None):
    return targets_service.list_hydrated(status=status)


@router.get("/api/targets/allocation")
def target_allocation():
    return targets_service.allocation_summary()


@router.post("/api/targets")
def create_target(body: TargetCreate):
    try:
        t = targets_service.create_target(body.model_dump(exclude_none=True))
    except (ValueError, ValidationError) as e:
        raise HTTPException(400, str(e))
    return targets_service.get_hydrated(t["id"])


@router.get("/api/targets/{target_id}")
def get_target(target_id: str):
    hydrated = targets_service.get_hydrated(target_id)
    if hydrated is None:
        raise HTTPException(404, "target not found")
    return hydrated


@router.put("/api/targets/{target_id}")
def update_target(target_id: str, body: TargetUpdate):
    changes = body.model_dump(exclude_unset=True)
    try:
        updated = targets_service.update_target(target_id, changes)
    except ValidationError as e:
        raise HTTPException(400, str(e))
    if updated is None:
        raise HTTPException(404, "target not found")
    return targets_service.get_hydrated(target_id)


@router.delete("/api/targets/{target_id}")
def delete_target(target_id: str):
    ok = targets_service.targets_store.delete(target_id)
    if not ok:
        raise HTTPException(404, "target not found")
    return {"ok": True}
