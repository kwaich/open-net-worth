"""Desktop-only scoped resources; financial writes stay in the existing import review."""

from __future__ import annotations

import asyncio
from typing import Literal

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request, UploadFile
from pydantic import BaseModel, Field

from backend.core.errors import OpenNetWorthError, to_http_status
from backend.core.version import APP_VERSION
from backend.services import alert_feed, desktop_access


def require_desktop(request: Request) -> None:
    if not getattr(request.state, "desktop_authenticated", False):
        raise HTTPException(403, "desktop host required")


router = APIRouter(prefix="/api/desktop", dependencies=[Depends(require_desktop)])


class FolderGrant(BaseModel):
    id: str = Field(min_length=8, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    label: str = Field(min_length=1, max_length=120)


class ImportReview(BaseModel):
    status: Literal["reviewed"]


@router.get("/status")
def status(request: Request):
    return {"protocol": 1, "instance_id": request.state.desktop_instance_id, "version": APP_VERSION}


@router.get("/alerts")
def alerts(limit: int = Query(alert_feed.MAX_EVENTS, ge=1, le=alert_feed.MAX_EVENTS)):
    return alert_feed.recent_alerts(limit=limit)


@router.get("/folders")
def folders():
    return desktop_access.list_folders()


@router.post("/folders")
def register_folder(body: FolderGrant):
    try:
        return desktop_access.register_folder(body.id, body.label)
    except OpenNetWorthError as exc:
        raise HTTPException(to_http_status(exc), exc.message) from exc


@router.delete("/folders/{grant_id}")
def revoke_folder(grant_id: str):
    try:
        return desktop_access.revoke_folder(grant_id)
    except OpenNetWorthError as exc:
        raise HTTPException(to_http_status(exc), exc.message) from exc


@router.get("/imports")
def imports():
    return desktop_access.list_imports()


@router.patch("/imports/{job_id}")
def mark_reviewed(job_id: str, body: ImportReview):
    try:
        return desktop_access.mark_reviewed(job_id)
    except OpenNetWorthError as exc:
        raise HTTPException(to_http_status(exc), exc.message) from exc


@router.post("/imports")
async def ingest(file: UploadFile, grant_id: str = Form(...)):
    content = await file.read(desktop_access.MAX_UPLOAD_BYTES + 1)
    if len(content) > desktop_access.MAX_UPLOAD_BYTES:
        raise HTTPException(413, "file too large")
    try:
        return await asyncio.to_thread(
            desktop_access.ingest_file,
            grant_id,
            file.filename or "",
            file.content_type or "",
            content,
        )
    except OpenNetWorthError as exc:
        raise HTTPException(to_http_status(exc), exc.message) from exc
