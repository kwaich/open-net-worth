"""
Investment activity API — thin router over services.activity.

Routes:
    GET /api/investments/activity
        ?type=buy,sell,dividend  &date_from=YYYY-MM-DD  &date_to=YYYY-MM-DD
        &account_id=…  &limit=50  &offset=0

Response: ActivityFeed { base, events: [ActivityRow], summary: { count_30d, … } }
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from backend.services import activity as activity_service

router = APIRouter()

_VALID_TYPES = {
    "buy",
    "sell",
    "transfer",
    "dividend",
    "coupon",
    "interest",
    "deposit",
    "withdrawal",
    "vest",
    "fee",
}


@router.get("/api/investments/activity")
def get_activity(
    type: Optional[str] = Query(None, description="Comma-separated activity types"),
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    account_id: Optional[str] = None,
    limit: Optional[int] = Query(None, ge=1, le=1000),
    offset: Optional[int] = Query(None, ge=0),
):
    """Unified investment-activity feed across buys, sells, dividends, vests, and fees.
    All amount_base values are recomputed from native legs + current FX."""
    type_filter: list[str] | None = None
    if type:
        types = [t.strip() for t in type.split(",") if t.strip()]
        invalid = [t for t in types if t not in _VALID_TYPES]
        if invalid:
            raise HTTPException(
                422,
                f"Unknown activity type(s): {invalid}. Valid values: {sorted(_VALID_TYPES)}",
            )
        type_filter = types or None

    return activity_service.activity_feed(
        type_filter=type_filter,
        date_from=date_from,
        date_to=date_to,
        account_id=account_id,
        limit=limit,
        offset=offset,
    )
