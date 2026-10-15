"""
GET    /api/intel/signals              — list intel signals (live feed; ?include_archived=true adds the archive)
GET    /api/intel/signals/{id}         — ONE signal, FULL unbounded timeline (F11 fix — the
                                          list route above bounds each embedded timeline)
PATCH  /api/intel/signals/{id}         — set lifecycle flags (read / dismissed / resolved)
POST   /api/intel/signals/read-all     — mark every live-feed signal read
DELETE /api/intel/signals/{id}         — permanent hard delete
"""

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.services import intel_stories as svc

router = APIRouter()


class SignalState(BaseModel):
    read: Optional[bool] = None
    dismissed: Optional[bool] = None
    resolved: Optional[bool] = None


@router.get("/api/intel/signals")
def get_intel_signals(include_archived: bool = False):
    return svc.list_signals(include_archived=include_archived)


@router.get("/api/intel/signals/{signal_id}")
def get_intel_signal(signal_id: str):
    """ONE signal with its FULL, unbounded timeline (F11 fix) — the "show all" fetch for a
    story whose list-embedded timeline was truncated at `list_signals`' own bounded default."""
    signal = svc.get_signal(signal_id)
    if signal is None:
        raise HTTPException(status_code=404, detail="Signal not found")
    return signal


@router.patch("/api/intel/signals/{signal_id}")
def patch_intel_signal(signal_id: str, state: SignalState):
    updated = svc.set_state(
        signal_id,
        read=state.read,
        dismissed=state.dismissed,
        resolved=state.resolved,
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="Signal not found")
    return updated


@router.post("/api/intel/signals/read-all")
def mark_all_intel_signals_read():
    return {"updated": svc.mark_all_read()}


@router.delete("/api/intel/signals/{signal_id}")
def delete_intel_signal(signal_id: str):
    return {"ok": svc.delete_signal(signal_id)}
