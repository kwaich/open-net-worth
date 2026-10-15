"""GET /api/market-ticker — commodity / coin / FX rows for the topbar ticker.

Thin per CLAUDE.md: validate nothing (no input), call ONE service, shape the response.
"""

from fastapi import APIRouter

from backend.services.market_ticker import market_ticker_rows

router = APIRouter()


@router.get("/api/market-ticker")
def get_market_ticker() -> dict:
    """Market rows the owner does not necessarily hold — gold, oil, BTC, FX pairs.

    The HOLDINGS half of the ticker is not served here: the frontend already has every
    position and its day move from `/api/portfolio`, so re-deriving it server-side would be a
    second source of truth for the same numbers.
    """
    return {"rows": market_ticker_rows()}
