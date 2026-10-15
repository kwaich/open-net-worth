"""
Fees-summary + portfolio X-ray (look-through) read/config API (P2-INV5).

A THIN router: it marshals input and shapes the response; every fee/exposure figure and
all FX normalization is computed in services.lots / services.xray (the math homes), never
here. Respects the dependency rule (api → services → db → core): the constituents validation
is delegated to services.xray.validate_constituents and the persist to InstrumentStore.

Routes:
    GET /api/fees-summary                      — trading fees paid (acquisition + disposal)
    GET /api/portfolio/xray                     — look-through sector/region exposure + unmapped%
    PUT /api/instruments/{id}/constituents      — set a fund's look-through map (checkpoint-covered)

The constituents edit is a plain InstrumentStore write (config, not money — written directly
like the rules routes), so it is checkpoint-covered automatically via PayloadTableStore's
row-level write_log (core.io.fire_db_write_hook) — the DB-store analogue of the retired
JsonStore._save_raw → fire_write_hook file path.

See .claude/rfcs/phase2/inv5-fees-xray.md §5.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.core.errors import OpenNetWorthError, to_http_status
from backend.db.instrument_store import InstrumentStore
from backend.models.instrument import Constituent
from backend.services import lots as lots_service
from backend.services import xray as xray_service

router = APIRouter()


@router.get("/api/fees-summary")
def get_fees_summary(
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    instrument_id: Optional[str] = None,
    account_id: Optional[str] = None,
):
    """Trading fees already paid (acquisition lot fees + disposal fees), normalized to the
    base currency from native legs + current FX (never a stored snapshot). Informational —
    these are NOT subtracted from realized gains again (they already amortize into basis /
    reduce proceeds). Returns {base_currency, acquisition_fees_base, disposal_fees_base,
    total_fees_base, by_instrument}."""
    return lots_service.fees_summary(
        date_from=date_from,
        date_to=date_to,
        instrument_id=instrument_id,
        account_id=account_id,
    )


@router.get("/api/portfolio/xray")
def get_portfolio_xray():
    """Look-through portfolio exposure: each holding exploded across its `constituents` map
    (an ETF → its weights; a plain stock → 100% self), aggregated portfolio-wide by sector
    and region with an honest unmapped %. Distinct from RiskPanel's whole-position sector
    view. Empty book → all-zeros."""
    return xray_service.portfolio_xray()


class ConstituentsBody(BaseModel):
    constituents: list[Constituent] = []


@router.put("/api/instruments/{instrument_id}/constituents")
def set_constituents(instrument_id: str, body: ConstituentsBody):
    """Set a fund's look-through map (the exposure breakdown). Validates weights via
    services.xray.validate_constituents (≥0, Σ ≤ 1, sector and/or region present), normalizes,
    and persists on the instrument (checkpoint-covered). `[]` clears the map. 404 unknown id;
    422 bad weights."""
    store = InstrumentStore()
    if not store.find_by_id(instrument_id):
        raise HTTPException(404, "instrument not found")
    raw = [c.model_dump() for c in body.constituents]
    try:
        clean = xray_service.validate_constituents(raw)
    except OpenNetWorthError as exc:
        raise HTTPException(to_http_status(exc), exc.message or str(exc)) from exc
    return store.set_constituents(instrument_id, clean)
