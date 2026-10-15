"""
GET /api/fire — FIRE projection. Thin router; all math lives in services/fire.py.
"""

from fastapi import APIRouter, HTTPException, Query

from backend.core.errors import ValidationError
from backend.services.fire import _load_profile, compute_fire, derive_fire_inputs

router = APIRouter()


@router.get("/api/fire")
def get_fire(
    current_nw: float = Query(..., description="Current total net worth in the base currency"),
):
    profile = _load_profile()
    inputs = derive_fire_inputs(profile)

    volatility_pct = float(profile.get("fire_volatility_pct") or 15.0)

    try:
        result = compute_fire(current_nw=current_nw, **inputs)
    except ValidationError as exc:
        raise HTTPException(422, str(exc)) from exc

    result["volatility_pct"] = round(volatility_pct, 2)
    result["life_expectancy"] = inputs["life_expectancy"]
    return result
