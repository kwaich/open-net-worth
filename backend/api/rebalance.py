"""
Rebalancing recommendations read API (P4-RB1).

A THIN router over ``services.rebalance`` — given the live investment book + the profile's
``target_allocation``, it returns current-vs-target weights, drift, and the suggested buy/sell
amounts (in base) to reach targets. **Compute-only, approve-gated by construction**: it
SUGGESTS trades as data and NEVER places an order or writes a position — acting on a suggestion
flows through the existing position propose→confirm→apply path.

Two shapes (same response):
    GET  /api/rebalance   — query-string what-ifs (new_cash, mode)
    POST /api/rebalance   — body what-ifs (a new-cash slider / mode toggle in one call)

Never 500s on a fresh instance: no targets set → ``status="no_targets"`` + zeros; empty book →
``status="empty_book"``. All allocation/trade math lives in the service; this layer only shapes
the request/response (api → services → core).
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Query
from pydantic import BaseModel

from backend.services import rebalance as rebalance_service

router = APIRouter()


class RebalanceParams(BaseModel):
    """What-if overrides for POST /api/rebalance. ``new_cash`` is fresh capital to deploy;
    ``mode`` is ``sell_to_target`` (full rebalance) | ``cash_only`` (buys only, never sell)."""

    new_cash: Optional[float] = None
    mode: Optional[str] = None


@router.get("/api/rebalance")
def get_rebalance(
    new_cash: Optional[float] = Query(None, description="Fresh capital to deploy (base currency)"),
    mode: Optional[str] = Query(None, description="sell_to_target | cash_only"),
):
    """Rebalancing plan over the live investment book. Returns current/target weights, drift,
    and suggested buy/sell trades (suggestions only — never executed). Never 500s."""
    return rebalance_service.rebalance_plan(
        new_cash=new_cash or 0.0,
        mode=mode or "sell_to_target",
    )


@router.post("/api/rebalance")
def post_rebalance(params: RebalanceParams):
    """Body form of the rebalancing plan (a new-cash slider / mode toggle in one call)."""
    return rebalance_service.rebalance_plan(
        new_cash=params.new_cash or 0.0,
        mode=params.mode or "sell_to_target",
    )
