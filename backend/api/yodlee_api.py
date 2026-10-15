"""Yodlee connector REST — global bank aggregation control (P3-CONN aggregation).

Thin router over ``db.YodleeConfigStore``:
  * ``GET  /api/yodlee/status``    — the public (non-secret) config + stats.
  * ``PUT  /api/yodlee/config``    — set the API credentials (client_id + write-only
    secret), user credentials (username + write-only password), the api_endpoint
    (sandbox|production), enable/disable. A blank secret or password leaves the saved one
    unchanged (write-only-secret discipline).

Auth is the app-wide ``require_auth`` dependency (this is under ``/api``). The secret,
password, and session tokens are SECRETS — accepted (write-only) but NEVER returned
(the status echoes only ``has_secret`` / ``has_user_credentials``). Yodlee is disabled by
policy (ADR ``2026-08-14-hide-yodlee-enterprise-only-pricing``) — its
``yodlee_fetch_transactions`` tool is registered but hidden/refused
(``ai.tools._connector_gate.DISABLED_CONNECTORS``).
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel

from backend.db.yodlee_config_store import YodleeConfigStore

router = APIRouter()


class YodleeConfigUpdate(BaseModel):
    client_id: Optional[str] = None
    # Write-only — blank/omitted keeps the saved secret.
    secret: Optional[str] = None
    username: Optional[str] = None
    # Write-only — blank/omitted keeps the saved password.
    password: Optional[str] = None
    api_endpoint: Optional[str] = None  # sandbox | production
    enabled: Optional[bool] = None


@router.get("/api/yodlee/status")
def yodlee_status():
    """Public config + sync stats (never the secret / password / session tokens —
    only ``has_*`` flags)."""
    return YodleeConfigStore().public()


@router.put("/api/yodlee/config")
def update_yodlee_config(body: YodleeConfigUpdate):
    """Configure the connector — credentials, api_endpoint, enable/disable. A blank
    ``secret`` or ``password`` keeps the saved one (write-only). Returns the public
    status."""
    store = YodleeConfigStore()
    changes: dict = {}
    if body.client_id is not None:
        changes["client_id"] = body.client_id
    if body.secret is not None:
        changes["secret"] = body.secret
    if body.username is not None:
        changes["username"] = body.username
    if body.password is not None:
        changes["password"] = body.password
    if body.api_endpoint is not None:
        changes["api_endpoint"] = body.api_endpoint
    if body.enabled is not None:
        changes["enabled"] = bool(body.enabled)

    store.update(changes)
    return store.public()
