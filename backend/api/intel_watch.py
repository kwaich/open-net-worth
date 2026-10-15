"""
GET /api/intel/watch-pulse?population=watched|held
GET /api/intel/watch-brief?list_id=…&period=1M

The Intel tab's watchlist strip: today's move, 52-week position, flagged findings, alert proximity
and portfolio fit for every watched-not-held ticker, ranked by attention. Deterministic, read-only.
The watch brief is a list tab's level-5 brief, written by Lucius (interactive-only, cached).
"""

from __future__ import annotations

import asyncio
from typing import Literal

from fastapi import APIRouter, HTTPException, Query

from backend.core.errors import NotFoundError

router = APIRouter()


@router.get("/api/intel/watch-pulse")
async def get_watch_pulse(population: Literal["watched", "held"] = Query(default="watched")):
    from backend.services.watch_pulse import build_pulse

    # OFF THE LOOP: one batched Yahoo quote plus store reads, all synchronous.
    return await asyncio.to_thread(build_pulse, None, population)


@router.get("/api/intel/watch-brief")
async def get_watch_brief(
    list_id: str = Query(min_length=1),
    period: Literal["1M", "3M", "1Y"] = Query(default="1M"),
):
    """``{narration, lean?, lean_reason?, suggestion?}`` — narration is None when the model is
    unavailable or its write-up failed the checks (the tab then keeps its level-4 template)."""
    from backend.monitoring import ai_opinion
    from backend.monitoring.brief_narration import watchlist_brief
    from backend.services.watchlist import read_watchlist

    config = await asyncio.to_thread(lambda: read_watchlist().get("config") or {})
    try:
        out = await watchlist_brief(list_id, period, ai_opinion.level_of(config))
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return out or {"narration": None}
