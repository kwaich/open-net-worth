"""
Instruments API — the instrument master + the resolve flow that turns a user-typed
ticker/coin into a canonical instrument with the right price source.

`resolve_instrument()` (the find-or-create + dedup-ladder logic, including the crypto
pair canonicalization and the identifiers-bag dedup ladder) lives in
backend/services/instruments.py — this router is a thin translation layer: validate the
request body, call the ONE service implementation, shape the HTTP response. The AI
`resolve_instrument` tool (ai/tools/portfolio.py) calls the same service function.

See .claude/rfcs/instrument-position-data-model.md (Phase 2) and
docs/adr/2026-07-15-instrument-identity-canonicalization-front-door.md.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.core.errors import OpenNetWorthError, to_http_status
from backend.db.instrument_store import InstrumentStore
from backend.services.instruments import resolve_instrument

router = APIRouter()


# ── API ───────────────────────────────────────────────────────────────────────


@router.get("/api/instruments")
def list_instruments():
    return InstrumentStore().all()


@router.post("/api/instruments/enrich-sectors")
def enrich_sectors():
    """Backfill GICS sectors onto yahoo-priced instruments (for the concentration risk lens).
    Idempotent and a no-op once every equity is classified — safe for the frontend to fire once
    on load. Best-effort: never raises."""
    try:
        from backend.api.prices import enrich_instrument_sectors

        return enrich_instrument_sectors()
    except Exception as e:
        return {"checked": 0, "updated": 0, "error": str(e)}


class ResolveBody(BaseModel):
    query: str
    asset_class: Optional[str] = None
    price_source: Optional[str] = None
    price_source_id: Optional[str] = None
    # Required when price_source='manual' (no feed to fetch a currency from — a missing
    # one is a 422, never a guessed USD); ignored on a feed-backed row, where the feed's
    # own answer always wins (see services.instruments._pin_facts).
    quote_currency: Optional[str] = None
    unit: Optional[str] = None
    asset_subclass: Optional[str] = None
    # Confirms a symbol collision against an existing DIFFERENT-vendor instrument is
    # genuinely a distinct asset (e.g. the same ticker on a different exchange) — see
    # services.instruments.find_existing. Default False: the first collision is refused
    # (422) rather than silently duplicated or silently reused.
    allow_duplicate_symbol: bool = False


@router.post("/api/instruments/resolve")
def resolve(body: ResolveBody):
    try:
        inst, created = resolve_instrument(
            body.query,
            body.asset_class,
            body.price_source,
            body.price_source_id,
            body.unit,
            body.asset_subclass,
            allow_duplicate_symbol=body.allow_duplicate_symbol,
            quote_currency=body.quote_currency,
        )
    except OpenNetWorthError as exc:  # symbol-collision refusal → 422 with a clear message
        raise HTTPException(to_http_status(exc), exc.message or str(exc)) from exc
    if inst is None:
        return {
            "resolved": False,
            "message": f"Couldn't resolve '{body.query}' to a tradeable instrument. "
            f"Try a ticker symbol (e.g. AAPL, ONWAI.DE) or a coin name, or pass "
            f"price_source + price_source_id explicitly.",
        }
    return {"resolved": True, "created": created, "instrument": inst}


@router.post("/api/instruments/enrich-classification")
def enrich_classification():
    """Backfill sector/region classification onto instruments using the heuristic engine.
    Idempotent; never overwrites manual or source-level classifications. Best-effort."""
    try:
        from backend.services.classification import enrich_instrument_classification

        return enrich_instrument_classification()
    except Exception as e:
        return {"checked": 0, "updated": 0, "error": str(e)}


class ClassificationOverrideBody(BaseModel):
    sector: Optional[str] = None
    region: Optional[str] = None


_CTRL_RE = re.compile(r"[\x00-\x1f\x7f]")
_MAX_CLASSIFICATION_LEN = 64


def _clean_classification(value: str) -> str:
    """Strip control characters and cap to 64 chars."""
    return _CTRL_RE.sub("", value)[:_MAX_CLASSIFICATION_LEN]


@router.put("/api/instruments/{instrument_id}/classification")
def set_classification(instrument_id: str, body: ClassificationOverrideBody):
    """Manually override the sector and/or region on an instrument.
    Sets classification_source='manual' so the enricher never overwrites it.
    Returns the updated instrument or 404 if the id is unknown."""
    store = InstrumentStore()
    inst = store.find_by_id(instrument_id)
    if inst is None:
        raise HTTPException(status_code=404, detail=f"Instrument {instrument_id!r} not found")

    now_iso = datetime.now(timezone.utc).isoformat()
    changes: dict = {"classification_source": "manual", "classification_updated_at": now_iso}
    if body.sector is not None:
        changes["sector"] = _clean_classification(body.sector)
    if body.region is not None:
        changes["region"] = _clean_classification(body.region)

    updated = store.touch(instrument_id, changes)
    if updated is None:
        raise HTTPException(status_code=404, detail=f"Instrument {instrument_id!r} not found")
    return updated
