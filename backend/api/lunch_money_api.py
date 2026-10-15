"""Lunch Money connector REST — transaction aggregation control.

Thin router over ``db.LunchMoneyConfigStore``:
  * ``GET  /api/lunch-money/status`` — the public (non-secret) config + stats.
  * ``PUT  /api/lunch-money/config`` — set the Lunch Money **access token**, enable/disable.
    A blank access token leaves the saved one unchanged (write-only-secret discipline).

Auth is the app-wide ``require_auth`` dependency (this is under ``/api``). The access
token is a SECRET — accepted (write-only) but NEVER returned by this router (the status
echoes only ``has_access_token``; the operator reads it back through the separate
``GET /api/connectors/lunch_money/secret/access_token`` reveal path). Fetching + booking
rows is no longer a scheduled poll or a REST action — it happens only inside a Lucius turn
(``lunch_money_fetch_transactions`` + ``add_transactions``), per RFC
``connectors-as-data-sources``.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel

from backend.db.lunch_money_config_store import LunchMoneyConfigStore

router = APIRouter()


class LunchMoneyConfigUpdate(BaseModel):
    # The Lunch Money personal access token. Blank/omitted → keep the saved token.
    access_token: Optional[str] = None
    enabled: Optional[bool] = None


@router.get("/api/lunch-money/status")
def lunch_money_status():
    """Public config + sync stats (never the access token — only ``has_access_token``)."""
    return LunchMoneyConfigStore().public()


@router.put("/api/lunch-money/config")
def update_lunch_money_config(body: LunchMoneyConfigUpdate):
    """Configure the connector. A non-blank ``access_token`` is saved as-is (no live
    validation call — an invalid token surfaces on the next fetch as ``auth_error``, same
    as Plaid's ``secret`` field). ``enabled`` is a plain config patch. Returns the public
    status."""
    store = LunchMoneyConfigStore()
    changes: dict = {}

    token = (body.access_token or "").strip()
    if token:
        changes["access_token"] = token
        # A freshly-pasted token means the user wants it on (mirrors every sibling
        # connector's "a fresh credential enables by default" convention).
        if body.enabled is None:
            changes["enabled"] = True

    if body.enabled is not None:
        changes["enabled"] = bool(body.enabled)

    store.update(changes)
    return store.public()
