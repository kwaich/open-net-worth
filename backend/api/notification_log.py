"""
GET  /api/notification-log          — list notifications (optional ?type=&unread_only=)
POST /api/notification-log          — create a notification (used by monitoring engine)
PATCH /api/notification-log/{id}    — mark read / update
DELETE /api/notification-log/{id}   — delete one
DELETE /api/notification-log        — clear all
GET  /api/notification-log/unread_count

Thin router — all persistence + the notification schema live in NotificationLogStore
(backend/db), so creates go through the checkpoint write-hook like every other
store write and the notification shape is defined in exactly one place.

Renamed from /api/signals (2026-08-20, revision 0044) — see NotificationLogStore's
module docstring for why.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Query
from pydantic import BaseModel

from backend.db import NotificationLogStore

router = APIRouter()


# ---------- request models ----------


class SignalCreate(BaseModel):
    type: str = "news"
    severity: str = "info"
    title: str
    summary: str = ""
    portfolio_impact: str = ""
    action_items: list[str] = []
    affected_tickers: list[str] = []
    sources: list[dict] = []  # [{title, url}, ...]
    source_urls: list[str] = []  # legacy fallback
    trigger: str = "scheduled"


class SignalPatch(BaseModel):
    read: Optional[bool] = None


# ---------- endpoints ----------


@router.get("/api/notification-log")
def get_notification_log(
    type: Optional[str] = Query(None),
    unread_only: bool = Query(False),
    limit: int = Query(50),
):
    return NotificationLogStore().query_signals(type=type, unread_only=unread_only, limit=limit)


@router.get("/api/notification-log/unread_count")
def get_unread_count():
    return {"count": NotificationLogStore().unread_count()}


@router.post("/api/notification-log", status_code=201)
def create_signal(body: SignalCreate):
    return NotificationLogStore().append(body.model_dump())


@router.patch("/api/notification-log/{signal_id}")
def patch_signal(signal_id: str, body: SignalPatch):
    store = NotificationLogStore()
    if body.read is None:
        existing = store.find_by_id(signal_id)
        return existing if existing is not None else ({"error": "not found"}, 404)
    updated = store.update_by_id(signal_id, {"read": body.read})
    return updated if updated is not None else ({"error": "not found"}, 404)


@router.patch("/api/notification-log/read_all")
def mark_all_read():
    NotificationLogStore().mark_all_read()
    return {"ok": True}


@router.delete("/api/notification-log/{signal_id}")
def delete_signal(signal_id: str):
    return {"ok": NotificationLogStore().delete(signal_id)}


@router.delete("/api/notification-log")
def clear_signals():
    NotificationLogStore().clear()
    return {"ok": True}


# ── Inbox dismiss endpoints (write path for the Inbox's notification rows) ───────
# These mark in-app notifications read via NotificationLogStore (so they funnel through
# the same store write path + checkpoint hook); the Inbox's GET stays read-only by
# design. URL paths stay /api/inbox/signals/* on purpose — they're owned by the Inbox
# tab (a separate, later teardown), not by this store's name.


@router.post("/api/inbox/signals/{signal_id}/dismiss")
def dismiss_inbox_signal(signal_id: str):
    """Dismiss one notification by marking it read (drops it from the unread inbox)."""
    return {"ok": bool(NotificationLogStore().mark_read(signal_id))}


@router.post("/api/inbox/signals/dismiss-all")
def dismiss_all_inbox_signals():
    """Mark all notifications read — the Inbox 'clear notifications' action."""
    NotificationLogStore().mark_all_read()
    return {"ok": True}
