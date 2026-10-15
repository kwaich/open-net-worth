import threading
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.services.actions import read_actions, write_actions

router = APIRouter()

# Serialises concurrent status-update calls (read-modify-write over the whole list) — a
# single-process guard only, matching the pre-Wave-5C (JSON->DB) file-backed behavior
# exactly (this app's multi-worker story is a separate, not-yet-addressed concern here).
_actions_lock: threading.Lock = threading.Lock()


@router.get("/api/actions")
def get_actions(commitment_id: Optional[str] = None):
    actions = read_actions()
    if commitment_id is not None:
        actions = [a for a in actions if a.get("commitment_id") == commitment_id]
    return actions


@router.get("/api/actions.json")
def get_actions_json():
    return read_actions()


class ActionStatusPatch(BaseModel):
    status: str


@router.patch("/api/actions/{action_id}/status")
def update_action_status(action_id: int, body: ActionStatusPatch):
    with _actions_lock:
        actions = read_actions()
        for a in actions:
            if a["id"] == action_id:
                a["status"] = body.status
                write_actions(actions)
                return {"ok": True}
    raise HTTPException(404, "Action not found")


# Legacy endpoint — kept for backward compat during migration
@router.post("/api/action-status")
def legacy_action_status(body: dict):
    with _actions_lock:
        actions = read_actions()
        for a in actions:
            if a["id"] == int(body["id"]):
                a["status"] = body["status"]
                write_actions(actions)
                return {"ok": True}
    raise HTTPException(404, "Action not found")
