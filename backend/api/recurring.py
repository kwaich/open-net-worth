"""
GET    /api/recurring                  — detected streams, candidates, excluded one-offs
POST   /api/recurring/{key}/status     — confirm / dismiss / reset a DETECTED stream

The read side of the cash-flow stream model (.claude/rfcs/2026-07-27-cashflow-stream-model.md).
Thin: validate → one service call → shape. Detection is never stored, so a GET always reflects
the current ledger; only the user's confirm/dismiss corrections persist.

Validation errors surface as the store's own field-prefixed plain strings ("status: must be one
of confirmed, dismissed"), not FastAPI's structured shape.
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
