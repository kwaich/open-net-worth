"""
GET /api/dashboard/layout — the Overview widget-dashboard layout (all views + active view)
PUT /api/dashboard/layout — replace the whole layout (position/size/config only)
"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.core.errors import ValidationError
from backend.services import dashboard as dashboard_service

router = APIRouter()


class DashboardLayoutBody(BaseModel):
    activeViewId: Optional[str] = None
    views: list[dict[str, Any]] = []


@router.get("/api/dashboard/layout")
def get_dashboard_layout() -> dict:
    return dashboard_service.get_layout()


@router.put("/api/dashboard/layout")
def put_dashboard_layout(body: DashboardLayoutBody) -> dict:
    try:
        return dashboard_service.save_layout(body.activeViewId, body.views)
    except ValidationError as exc:
        raise HTTPException(422, exc.message) from exc
