"""
GET    /api/recurring                  — detected + declared streams, candidates, excluded one-offs
POST   /api/recurring/{key}/status     — confirm / dismiss / reset a DETECTED stream
POST   /api/recurring/declared         — declare a stream history can't show yet
PATCH  /api/recurring/declared/{id}    — edit a declared stream
DELETE /api/recurring/declared/{id}    — remove a declared stream

The read side of the cash-flow stream model (.claude/rfcs/2026-07-27-cashflow-stream-model.md).
Thin: validate → one service call → shape. Detection is never stored, so a GET always reflects
the current ledger; only the user's corrections and declarations persist.

Validation errors surface as the store's own field-prefixed plain strings ("amount: must be a
number"), not FastAPI's structured shape — these route verbatim into chat and the UI.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.db.recurring_stream_store import RecurringStreamStore
from backend.services import ledger as ledger_service
from backend.services.fx_cache import load_fx_cache
from backend.services.recurring_baseline import resolve_baseline

router = APIRouter()


class StatusBody(BaseModel):
    status: str  # confirm | dismiss | reset
    note: Optional[str] = None


class DeclaredBody(BaseModel):
    """Loosely typed on purpose — ``RecurringStreamStore._validated_declared`` owns the rules
    and returns the plain field-prefixed strings the UI/chat render verbatim."""

    direction: Optional[str] = None
    label: Optional[str] = None
    amount: Optional[float] = None
    currency: Optional[str] = None
    cadence: Optional[str] = None
    anchor_date: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    note: Optional[str] = None


@router.get("/api/recurring")
def get_recurring() -> dict:
    base = ledger_service.base_currency()
    result = resolve_baseline(base, load_fx_cache())
    return {**result, "base_currency": base}


@router.post("/api/recurring/{key}/status")
def set_status(key: str, body: StatusBody) -> dict:
    action = (body.status or "").strip().lower()
    store = RecurringStreamStore()
    if action == "reset":
        return {"ok": True, "key": key, "status": "detected", "reset": store.reset(key)}
    mapped = {"confirm": "confirmed", "dismiss": "dismissed"}.get(action, action)
    try:
        row = store.set_status(key, mapped, body.note or "")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    return {"ok": True, "key": key, "status": row["status"]}


@router.post("/api/recurring/declared")
def create_declared(body: DeclaredBody) -> dict:
    try:
        row = RecurringStreamStore().create_declared(body.model_dump(exclude_none=True))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    return {"ok": True, "stream": row}


@router.patch("/api/recurring/declared/{stream_id}")
def update_declared(stream_id: str, body: DeclaredBody) -> dict:
    try:
        row = RecurringStreamStore().update_declared(stream_id, body.model_dump(exclude_none=True))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    if not row:
        raise HTTPException(status_code=404, detail=f"No declared stream {stream_id}")
    return {"ok": True, "stream": row}


@router.delete("/api/recurring/declared/{stream_id}")
def delete_declared(stream_id: str) -> dict:
    if not RecurringStreamStore().delete_declared(stream_id):
        raise HTTPException(status_code=404, detail=f"No declared stream {stream_id}")
    return {"ok": True, "deleted": stream_id}
