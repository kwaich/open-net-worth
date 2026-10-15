"""Alpaca brokerage connector REST — holdings READ sync (alpaca-connector RFC S1).

Thin router over ``db.AlpacaConfigStore`` + ``services.aggregation.alpaca``:

  * ``GET  /api/alpaca/status`` — the public (non-secret) config + sync stats.
  * ``PUT  /api/alpaca/config`` — set the API key id + secret (write-only, blank=keep),
    environment (paper|live), enable READ sync. A non-blank key pair is VALIDATED by a probe
    ``GET /v2/account`` so a bad key fails at config time, not the first fetch.

Auth is the app-wide ``require_auth`` dependency. The key pair is a SECRET — accepted
write-only, NEVER returned (status echoes only ``has_keys``). Fetching + syncing holdings
is no longer a scheduled poll or a REST action — it happens only inside a Lucius turn
(``alpaca_fetch_positions`` + the position tools), per RFC ``connectors-as-data-sources``.

This connector is READ-ONLY. The S2 trading half (``PUT /api/alpaca/trading`` + the
propose/apply order tools) was REMOVED 2026-07-19 — see
``docs/adr/2026-07-19-remove-alpaca-trading-half.md``. Trading goes through Alpaca's own MCP
server via the external-MCP path, which already forces confirmation on money movement.
"""

from __future__ import annotations

from typing import Optional

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.db import alpaca_config_store as alpaca_store
from backend.db.alpaca_config_store import AlpacaConfigStore
from backend.services.aggregation import alpaca as alpaca_service

router = APIRouter()


class AlpacaConfigUpdate(BaseModel):
    # Write-only secrets — blank/omitted means "keep the saved key".
    api_key_id: Optional[str] = None
    secret_key: Optional[str] = None
    environment: Optional[str] = None  # paper | live
    enabled: Optional[bool] = None  # READ sync


def _probe_keys(base_url: str, key_id: str, secret: str) -> None:
    """Validate a key pair with a probe ``GET /v2/account`` (RFC §5.1) so a bad key fails at
    config time. Raises HTTPException(422) on auth failure / unreachable endpoint."""
    try:
        alpaca_service.fetch_account(base_url, key_id, secret)
    except httpx.HTTPStatusError as exc:
        code = exc.response.status_code
        if code in (401, 403):
            raise HTTPException(
                422,
                "Alpaca rejected those credentials (check the key/secret and that they match "
                "the selected paper/live environment).",
            ) from exc
        raise HTTPException(422, f"Alpaca returned HTTP {code} validating the key.") from exc
    except httpx.HTTPError as exc:
        raise HTTPException(422, f"Could not reach Alpaca to validate the key: {exc}") from exc


@router.get("/api/alpaca/status")
def alpaca_status():
    """Public config + sync stats (never the key pair — only ``has_keys``)."""
    return AlpacaConfigStore().public()


@router.put("/api/alpaca/config")
def update_alpaca_config(body: AlpacaConfigUpdate):
    """Configure the connector. A non-blank key pair is PROBED before saving (bad key → 422).
    ``environment`` / ``enabled`` are plain config patches. Both keys must be set together.
    Returns the public status."""
    store = AlpacaConfigStore()
    changes: dict = {}

    key_id = (body.api_key_id or "").strip()
    secret = (body.secret_key or "").strip()
    # Both-or-neither: a half-set credential is rejected (mirrors the store's discipline).
    if bool(key_id) != bool(secret):
        raise HTTPException(422, "Provide BOTH the API key id and secret, or neither.")

    # Resolve the environment we'd validate against (incoming change wins, else saved).
    env = (body.environment or "").strip().lower()
    if env and env not in ("paper", "live"):
        raise HTTPException(422, "environment must be 'paper' or 'live'.")
    effective_env = env or store.environment()
    base_url = (
        alpaca_store.LIVE_BASE_URL if effective_env == "live" else alpaca_store.PAPER_BASE_URL
    )

    if key_id and secret:
        _probe_keys(base_url, key_id, secret)
        changes["api_key_id"] = key_id
        changes["secret_key"] = secret
        if body.enabled is None:
            changes["enabled"] = True  # a freshly-validated key means the user wants it on

    if env:
        changes["environment"] = env
    if body.enabled is not None:
        changes["enabled"] = bool(body.enabled)

    store.update(changes)
    return store.public()
