"""SimpleFIN connector REST — bank/card account aggregation control (P3-CONN aggregation).

Thin router over ``db.SimpleFinConfigStore`` + ``services.aggregation.simplefin``:
  * ``GET  /api/simplefin/status`` — the public (non-secret) config + stats.
  * ``PUT  /api/simplefin/config`` — set a SimpleFIN **setup token** (claimed → an access
    URL is stored, write-only), enable/disable. A blank setup token leaves the saved access
    URL unchanged (write-only-secret discipline).

Auth is the app-wide ``require_auth`` dependency (this is under ``/api``). The access URL /
setup token are SECRETS — they are accepted (write-only) but NEVER returned (the status
echoes only ``has_access_url``). Fetching + booking rows is no longer a scheduled poll or a
REST action — it happens only inside a Lucius turn (``simplefin_fetch_transactions`` +
``add_transactions``), per RFC ``connectors-as-data-sources``.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.core.errors import OpenNetWorthError, to_http_status
from backend.db.simplefin_config_store import SimpleFinConfigStore
from backend.services.aggregation import simplefin as simplefin_service

router = APIRouter()


class SimpleFinConfigUpdate(BaseModel):
    # The one-time SimpleFIN setup token (base64). Blank/omitted → keep the saved creds.
    setup_token: Optional[str] = None
    enabled: Optional[bool] = None


@router.get("/api/simplefin/status")
def simplefin_status():
    """Public config + sync stats (never the access URL — only ``has_access_url``)."""
    return SimpleFinConfigStore().public()


@router.put("/api/simplefin/config")
def update_simplefin_config(body: SimpleFinConfigUpdate):
    """Configure the connector. A non-blank ``setup_token`` is CLAIMED for an access URL
    (one-time; a duplicate/invalid claim → 422). ``enabled`` is a plain config patch.
    Returns the public status."""
    store = SimpleFinConfigStore()
    changes: dict = {}

    token = (body.setup_token or "").strip()
    if token:
        try:
            access_url = simplefin_service.claim_setup_token(token)
        except OpenNetWorthError as exc:
            raise HTTPException(to_http_status(exc), exc.message or str(exc)) from exc
        changes["access_url"] = access_url
        # A freshly-claimed token means the user wants it on (mirror the env-seed default).
        if body.enabled is None:
            changes["enabled"] = True

    if body.enabled is not None:
        changes["enabled"] = bool(body.enabled)

    store.update(changes)
    return store.public()
