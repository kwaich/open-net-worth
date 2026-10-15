"""
GET /api/intel/digest?period=1M&narrate=false

Returns the deterministic Intel Brief digest. `narrate=true` adds a short LLM narration
(interactive-only path — a headless caller receives narrate=false regardless of the param).
"""

from __future__ import annotations

import asyncio
from typing import Literal

from fastapi import APIRouter, Query, Request

router = APIRouter()

_VALID_PERIODS = {"1M", "3M", "YTD", "1Y", "ALL"}


@router.get("/api/intel/digest")
async def get_intel_digest(
    request: Request,
    period: str = Query(default="1M"),
    narrate: bool = Query(default=False),
    style: Literal["line", "brief"] = Query(default="line"),
):
    from backend.services.intel_digest import build_digest

    p = period.upper()
    if p not in _VALID_PERIODS:
        p = "1M"

    # OFF THE LOOP. build_digest is fully synchronous and deep: it folds the whole
    # transaction table (portfolio_returns → portfolio_history.inception_date →
    # TransactionStore.all) and then scans price_history (PriceHistoryStore.as_of_maps).
    # This route is `async def`, so that ran ON the loop and stalled every other request —
    # measured 4.8s live 2026-08-11, with core.loop_watch naming both legs in the same
    # request and reporting a 4712ms stall across 17 waiting tasks.
    digest = await asyncio.to_thread(build_digest, period=p)

    # Narration is interactive-only: headless callers (agents, scheduled tasks) never
    # receive LLM narration even if they somehow pass narrate=true. The boundary is
    # the `from_headless` header that the scheduled runner sets (mirrors the chat gate).
    # We detect non-browser callers by the absence of a real Accept header or the
    # presence of the `X-Headless` header; safest to just block if the tool/agent path
    # calls this endpoint (the tool forces narrate=false anyway).
    if narrate and style == "brief":
        # The Holdings brief at verbosity 5 (Settings › Intelligence): a short narrative in the
        # user's opinion level, fact-checked and cached (monitoring.brief_narration).
        try:
            from backend.monitoring import ai_opinion
            from backend.monitoring.brief_narration import holdings_brief
            from backend.services.watchlist import read_watchlist

            config = await asyncio.to_thread(lambda: read_watchlist().get("config") or {})
            out = await holdings_brief(digest, ai_opinion.level_of(config)) or {}
            digest["narration"] = out.pop("narration", None)
            digest["narration_opinion"] = out or None
        except Exception:
            digest["narration"] = None
    elif narrate:
        try:
            from backend.monitoring.intel_digest_narration import narrate as _narrate

            digest["narration"] = await _narrate(digest)
        except Exception:
            digest["narration"] = None

    return digest
