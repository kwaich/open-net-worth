"""
Investments brief API — thin router over services.portfolio_brief.
Routes:
    GET /api/investments/brief
"""

from __future__ import annotations

from fastapi import APIRouter

from backend.services import portfolio_brief as brief_service

router = APIRouter()


@router.get("/api/investments/brief")
def get_investments_brief():
    """Portfolio brief signals computed server-side (concentration, rebalance, vesting, income, today, ontrack).
    Returns {signals, decisions_count, base_currency}. Degrades gracefully — never 500s."""
    return brief_service.compute_brief()
