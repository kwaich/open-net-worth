"""
Forecast-vs-actuals scorecard read API (P3-SI1).

A THIN router over ``services.forecast_scorecard`` — it validates the window enum and shapes
the response; every figure (the replayed projection, the per-month deltas, the accuracy/MAPE/
bias stats, FX normalization) is computed in the service (the math home), never here. Respects
the dependency rule: imports only the service (api -> services), no store / no math directly.

The scorecard is computed-not-stored — a pure read, no write choke point.

Route:
    GET /api/forecast-scorecard?window={1M|3M|6M|1Y}   (default 3M)

A bad window → 422 (FastAPI enum validation). An empty/young instance → 200 with
``forecastable: false`` + zeroed accuracy + ``periods: []`` (never 500), mirroring
/api/returns and /api/networth/history.
"""

from __future__ import annotations

from enum import Enum

from fastapi import APIRouter

from backend.services import forecast_scorecard as scorecard_service

router = APIRouter()


class Window(str, Enum):
    M1 = "1M"
    M3 = "3M"
    M6 = "6M"
    Y1 = "1Y"


@router.get("/api/forecast-scorecard")
def get_forecast_scorecard(window: Window = Window.M3):
    """Forecast-vs-actuals scorecard over the trailing window: did our cash-flow + net-worth
    projections match reality? Per-month projected-vs-actual deltas + accuracy (mean abs error,
    MAPE, signed bias). ``forecastable`` is false (zeroed figures, never 500) on a young/empty
    instance with no recurring baseline or no elapsed months. All math lives in the service."""
    return scorecard_service.scorecard(window.value)
