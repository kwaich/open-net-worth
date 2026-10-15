"""Human-only market-data settings and safe upstream health endpoints."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.services import market_data_settings

router = APIRouter()


class MarketDataConfigUpdate(BaseModel):
    # Write-only. Absent (the default) or "" keeps the saved secret; an explicit null clears it
    # — see MarketDataConfigStore.update.
    coingecko_api_key: Optional[str] = None
    # "demo" | "pro" — which CoinGecko host/header the key is validated against
    # (see ``services/coingecko_client.py``). Invalid/absent falls back to "demo".
    coingecko_api_key_tier: Optional[str] = None
    yahoo_primary_for_major_crypto: Optional[bool] = None


@router.get("/api/market-data/config")
def market_data_config():
    return market_data_settings.get_public_config()


@router.get("/api/market-data/status")
def market_data_status():
    return market_data_settings.get_public_status()


@router.put("/api/market-data/config")
def update_market_data_config(body: MarketDataConfigUpdate):
    try:
        return market_data_settings.update_public_config(body.model_dump(exclude_unset=True))
    except ValueError as error:
        raise HTTPException(422, "coingecko_api_key: invalid characters") from error
