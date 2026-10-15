import asyncio
import logging

from fastapi import APIRouter
from fastapi.responses import JSONResponse

router = APIRouter()
_log = logging.getLogger(__name__)

# All price-fetching logic now lives in services/prices.py; imported here so existing callers
# that reference api.prices.fetch_live / _NAMES_CACHE etc. continue to work.
from backend.services.fx_cache import load_fx_cache, save_fx_cache  # noqa: F401
from backend.services.prices import (  # noqa: E402, F401 — re-exports for callers
    _EXCHANGE_CACHE,
    _NAMES_CACHE,
    _SECTOR_CACHE,
    COINGECKO_IDS,
    _backfill_dividend_rates,
    _crypto_routing,
    _fetch_coingecko_by_id,
    _fetch_name,
    _fetch_one,
    _fetch_sector,
    enrich_instrument_sectors,
    fetch_live,
    load_names_cache,
    load_sectors_cache,
    save_names_cache,
    save_sectors_cache,
)


@router.get("/api/prices")
async def get_prices(tickers: str = "", fx: str = "", include_names: bool = False):
    ticker_list = [t.strip() for t in tickers.split(",") if t.strip()]
    currency_list = [c.strip() for c in fx.split(",") if c.strip()]
    try:
        # fetch_live is a real Yahoo/CoinGecko network round-trip (multiple seconds under
        # rate-limiting). Offload it via asyncio.to_thread (same pattern as the connector sync
        # routes) rather than a plain `def` route: FastAPI dispatches sync `def` handlers through
        # Starlette's shared request thread pool, which every other (cheap, CPU-bound) sync route
        # also uses — a slow price fetch there would queue unrelated requests behind it.
        # asyncio.to_thread runs on asyncio's own default executor, so it never contends for that
        # shared pool.
        data = await asyncio.to_thread(
            lambda: fetch_live(
                ticker_list, currency_list, include_names=include_names, allow_stale=True
            )
        )
    except Exception as e:
        data = {"prices": {}, "prev_prices": {}, "fx": {}, "resolved": {}, "error": str(e)}
    return JSONResponse(data)
