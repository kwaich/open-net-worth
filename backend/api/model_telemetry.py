"""GET /api/ai/model-telemetry — per-model latency + structured-output-retry snapshot."""

from __future__ import annotations

from fastapi import APIRouter

from backend.services import model_telemetry as model_telemetry_service

router = APIRouter()


@router.get("/api/ai/model-telemetry")
def get_model_telemetry():
    return {"models": model_telemetry_service.model_telemetry()}
