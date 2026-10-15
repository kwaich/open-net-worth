"""
REST API for Outlook plan-state persistence (saved scenarios + FIRE baseline).

    GET    /api/plan/scenarios              list saved scenarios
    POST   /api/plan/scenarios              create a scenario (id assigned server-side)
    PUT    /api/plan/scenarios/{id}         replace a scenario; 404 if missing
    DELETE /api/plan/scenarios/{id}         remove a scenario; 204; 404 if missing
    GET    /api/plan/baseline               the FIRE baseline override (or null)
    PUT    /api/plan/baseline               store or clear the baseline ({} / null = clear)

Field keys are camelCase throughout (stored verbatim, no server-side transformation).
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Response

from backend.core.errors import OpenNetWorthError, to_http_status
from backend.services import plan_state as svc

router = APIRouter()


def _call(fn, *args, **kwargs):
    from fastapi import HTTPException

    try:
        return fn(*args, **kwargs)
    except OpenNetWorthError as exc:
        raise HTTPException(to_http_status(exc), exc.message or str(exc)) from exc


@router.get("/api/plan/scenarios")
def list_scenarios():
    return {"scenarios": _call(svc.get_scenarios)}


@router.post("/api/plan/scenarios", status_code=201)
def create_scenario(body: Dict[str, Any]):
    return _call(svc.add_scenario, body)


@router.put("/api/plan/scenarios/{scenario_id}")
def update_scenario(scenario_id: str, body: Dict[str, Any]):
    return _call(svc.update_scenario, scenario_id, body)


@router.delete("/api/plan/scenarios/{scenario_id}", status_code=204)
def delete_scenario(scenario_id: str):
    _call(svc.delete_scenario, scenario_id)
    return Response(status_code=204)


@router.get("/api/plan/baseline")
def get_baseline():
    return {"baseline": _call(svc.get_baseline)}


@router.put("/api/plan/baseline")
def set_baseline(body: Optional[Dict[str, Any]] = None):
    # An empty dict or null both mean "clear the baseline override".
    obj = body if body else None
    return {"baseline": _call(svc.set_baseline, obj)}
