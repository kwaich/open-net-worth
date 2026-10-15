"""Options API — derivative overlay contracts (sys-options-data-model RFC).

Thin router over services.options: validate input → call one service → shape response.
All monetary math and lifecycle logic live in the service. Respects api → services.

Routes:
    GET  /api/options           — the options lens: all contracts valued/signed in base
    GET  /api/options/{id}      — one hydrated contract (mark, moneyness, days-to-expiry)
    POST /api/options           — open a new contract
    PATCH /api/options/{id}     — close / expire / update a contract
    DELETE /api/options/{id}    — remove a mistaken entry (open contracts only)
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from backend.services import options as options_service

router = APIRouter()


@router.get("/api/options")
def get_options():
    """Options book in the OptionsBook shape: {base_currency, summary, positions}.
    Positions are open contracts only, each shaped to the frontend OptionContract type.
    Empty book returns zeros in summary and positions=[]; never crashes."""
    return options_service.book()


@router.get("/api/options/{contract_id}")
def get_option(contract_id: str):
    """One hydrated option contract (mark, moneyness, intrinsic, days_to_expiry)."""
    result = options_service.get_contract(contract_id)
    if result is None:
        raise HTTPException(status_code=404, detail="option contract not found")
    return result


@router.post("/api/options")
def open_option(body: dict):
    """Open a new option contract. Required: account_id, underlying_instrument_id,
    option_type, side, strike, expiry, contracts.

    Idempotent on idempotency_key (broker order id). Double-book guard on
    (account, underlying, type, side, strike, expiry, contracts) unless allow_duplicate=true.

    If premium_account_id is supplied, bridges the cash premium leg through the
    ledger choke point (confirmed, FX-converted). Omitted → no leg."""
    result = options_service.apply_option_change({**body, "type": "open"})
    if "error" in result:
        # 409 for the identical-contract double-book refusal (existing_contract_id
        # present) so the caller can distinguish it and retry with allow_duplicate —
        # the api/lots.py acquire precedent.
        if result.get("existing_contract_id"):
            raise HTTPException(status_code=409, detail=result["error"])
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@router.patch("/api/options/{contract_id}")
def update_option(contract_id: str, body: dict):
    """Close, expire, or update a contract.

    body.type ∈ {"close", "expire", "update"}.
    - close: set status=closed, closed_at
    - expire: lifecycle flip (normally called by the sweep; can be used to manually
      expire a single contract by setting type="expire" and contract_id)
    - update: edit metadata (note, strategy_tag, collateral linkage)"""
    op = (body.get("type") or "update").lower()
    if op not in ("close", "expire", "update"):
        raise HTTPException(
            status_code=400,
            detail=f"invalid type {op!r}: must be 'close', 'expire', or 'update'",
        )
    result = options_service.apply_option_change({**body, "type": op, "contract_id": contract_id})
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@router.delete("/api/options/{contract_id}")
def delete_option(contract_id: str, force: bool = False):
    """Remove a mistaken entry. Only allowed while status='open' unless force=true."""
    result = options_service.apply_option_change(
        {"type": "delete", "contract_id": contract_id, "force": force}
    )
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return result
