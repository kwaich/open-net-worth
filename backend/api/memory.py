"""Memory API — the global toggle, plus reading and forgetting stored facts.

GET    /api/memory/settings   → {"enabled": bool}
PUT    /api/memory/settings   body {"enabled": bool} → updated settings
GET    /api/memory/facts      → {"facts": [...], "total": n}
DELETE /api/memory/facts/{id} → {"ok": bool}
DELETE /api/memory/facts      → {"ok": true, "deleted": n}

Thin: validate → store → shape response. No business logic.

Forgetting is deliberately reachable while memory is OFF: the toggle stops extraction and
injection, but a user who turned memory off and now wants the already-stored facts GONE must
not have to turn it back on to do that.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.db.memory_settings_store import MemorySettingsStore
from backend.db.memory_store import MemoryStore

router = APIRouter()


class MemorySettingsPatch(BaseModel):
    enabled: bool


@router.get("/api/memory/settings")
def get_memory_settings():
    return {"enabled": MemorySettingsStore().enabled()}


@router.put("/api/memory/settings")
def put_memory_settings(body: MemorySettingsPatch):
    return MemorySettingsStore().set_enabled(body.enabled)


@router.get("/api/memory/facts")
def list_memory_facts():
    """Every stored fact, newest-updated first. Bounded by the store's own size — memory is
    a distilled fact list, not a log, so there is no window to page through."""
    facts = MemoryStore().all_facts()
    return {"facts": facts, "total": len(facts)}


@router.delete("/api/memory/facts/{fact_id}")
def forget_memory_fact(fact_id: str):
    if not MemoryStore().remove_by_id(fact_id):
        raise HTTPException(status_code=404, detail="unknown memory")
    return {"ok": True, "id": fact_id}


@router.delete("/api/memory/facts")
def forget_all_memory_facts():
    store = MemoryStore()
    facts = store.all_facts()
    deleted = sum(1 for f in facts if store.remove_by_id(f.get("id", "")))
    return {"ok": True, "deleted": deleted}
