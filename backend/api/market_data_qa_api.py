"""QA-only deterministic market-data fixture controls."""

from __future__ import annotations

import hmac
import math
import os

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, field_validator

from backend.services import market_data_qa

router = APIRouter()


@router.get("/api/qa/attest")
def attest_qa_run(x_onw_qa_nonce: str | None = Header(default=None)):
    """Prove a fixture targets the exact disposable QA process launched for this run."""
    expected = os.environ.get("ONW_QA_NONCE", "")
    if not expected or not x_onw_qa_nonce or not hmac.compare_digest(x_onw_qa_nonce, expected):
        # Deliberately indistinguishable from an absent QA route.
        raise HTTPException(404, "Not Found")
    return {"qa_mode": True}


class MarketDataScenario(BaseModel):
    name: str
    clear_persistent: bool = False
    fx_rates: dict[str, float] | None = None

    @field_validator("fx_rates")
    @classmethod
    def validate_fx_rates(cls, value: dict[str, float] | None):
        if value is not None and (
            not value
            or any(
                not code.strip() or not math.isfinite(rate) or rate <= 0
                for code, rate in value.items()
            )
        ):
            raise ValueError("FX rates must have currency codes and finite positive values")
        return value


@router.post("/api/qa/market-data/scenario")
def set_market_data_scenario(body: MarketDataScenario):
    try:
        return market_data_qa.set_scenario(body.name, body.clear_persistent, body.fx_rates)
    except ValueError as error:
        raise HTTPException(422, "Unknown QA market-data scenario") from error


@router.get("/api/qa/market-data/status")
def market_data_status():
    return market_data_qa.snapshot()
