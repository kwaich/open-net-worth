"""IBKR Flex Web Service connector REST — READ-ONLY (ibkr-connector RFC).

Thin router over ``db.IbkrConfigStore`` + ``services.aggregation.ibkr``:

  * ``GET  /api/ibkr/status`` — the public (non-secret) config + sync stats.
  * ``PUT  /api/ibkr/config`` — set the Flex token (write-only, blank=keep) +
    query ID, enable READ sync.

There is NO order-placement route. ``PUT /api/ibkr/trading`` and the
``trading_enabled`` flag it flipped were REMOVED 2026-07-20 — they gated a
capability that never existed (see
``docs/adr/2026-07-20-remove-ibkr-dead-trading-flag.md``).

Auth is the app-wide ``require_auth`` dependency.  The Flex token is a SECRET —
accepted write-only, NEVER returned (status echoes only ``has_token``). Fetching +
booking rows is no longer a scheduled poll or a REST action — it happens only inside a
Lucius turn (``ibkr_fetch_activity`` + ``add_transactions``/the position tools), per RFC
``connectors-as-data-sources``.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.db.ibkr_config_store import IbkrConfigStore

router = APIRouter()


class IbkrConfigUpdate(BaseModel):
    # Write-only secret — blank/omitted means "keep the saved token".
    flex_token: Optional[str] = None
    flex_query_id: Optional[str] = None
    enabled: Optional[bool] = None  # READ sync


@router.get("/api/ibkr/status")
def ibkr_status():
    """Public config + sync stats (never the Flex token — only ``has_token``)."""
    return IbkrConfigStore().public()


@router.put("/api/ibkr/config")
def update_ibkr_config(body: IbkrConfigUpdate):
    """Configure the connector.

    ``flex_token`` and ``flex_query_id`` are stored; blank/omitted = keep saved.
    Enabling the connector without both fields raises 422. Returns the public status.
    """
    store = IbkrConfigStore()
    changes: dict = {}

    token = (body.flex_token or "").strip()
    query_id = (body.flex_query_id or "").strip()

    if token:
        changes["flex_token"] = token
    if query_id:
        changes["flex_query_id"] = query_id

    if body.enabled is not None:
        # If the user is trying to enable, verify the connector is configured.
        effective_token = token or store.flex_token()
        effective_qid = query_id or store.flex_query_id()
        if body.enabled and (not effective_token or not effective_qid):
            raise HTTPException(
                422,
                "Provide both a Flex token and a query ID before enabling IBKR sync.",
            )
        changes["enabled"] = bool(body.enabled)
    elif token and query_id:
        # A freshly-supplied pair means the user wants the connector on.
        changes.setdefault("enabled", True)

    store.update(changes)
    return store.public()
