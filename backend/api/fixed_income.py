"""
Fixed-income (bond) API — the fixed-income lens over the portfolio (bonds-first-class RFC).

A THIN router over services.fixed_income: it shapes the response only; all valuation,
coupon, and maturity math lives in the service (the math home), never here. Respects the
dependency rule (api → services).

Routes:
    GET /api/fixed-income  — bond holdings valued in base + by-subtype + maturity ladder
"""

from __future__ import annotations

from fastapi import APIRouter

from backend.services import fixed_income as fixed_income_service

router = APIRouter()


@router.get("/api/fixed-income")
def get_fixed_income():
    """Every bond holding valued in base currency, the by-subtype split, cumulative maturity
    buckets (≤30d/≤90d/≤1y) and the upcoming-maturity ladder. Empty portfolio ⇒ zeros."""
    return fixed_income_service.summary()
