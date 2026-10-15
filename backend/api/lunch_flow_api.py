"""Lunch Flow connector REST — OAuth-based bank/investment aggregation control.

Thin router over ``db.LunchFlowConfigStore`` + ``services.aggregation.lunch_flow[_oauth]``:

  * ``GET  /api/lunch-flow/status``          — the public (non-secret) config + stats.
  * ``PUT  /api/lunch-flow/config``          — set the developer credentials (client_id +
    write-only client_secret), the pre-fill email, enable/disable. A blank ``client_secret``
    leaves the saved one unchanged (write-only-secret discipline).
  * ``POST /api/lunch-flow/oauth/begin``     — start the bank-link sign-in → returns the Lunch
    Flow hosted authorize URL to open (popup+poll UX, mirroring the MCP OAuth flow's shape).
  * ``GET  /api/lunch-flow/oauth/{flow_id}`` — poll a staged flow's outcome. NEVER a token.
  * ``GET  /api/lunch-flow/oauth/callback``  — the vendor's browser redirect back to us.
    Auth-EXEMPT (``api/_deps.py``'s ``_EXEMPT``) — same reasoning as the MCP OAuth callback
    (ADR ``2026-08-16-mcp-oauth-callback-is-auth-exempt-state-is-the-credential.md``): the
    single-use ``state`` is the credential, not a live session.
  * ``DELETE /api/lunch-flow/connections/{connection_id}`` — unlink one institution.

Auth is the app-wide ``require_auth`` dependency for every route except the callback (this is
under ``/api``). The client secret / OAuth tokens are SECRETS — accepted (write-only, and only
the secret at that — the tokens are never operator-typed) but NEVER returned. Fetching +
booking rows is no longer a scheduled poll or a REST action — it happens only inside a
Lucius turn (``lunch_flow_fetch_transactions`` + ``add_transactions``), per RFC
``connectors-as-data-sources``.
"""

from __future__ import annotations

import asyncio
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from backend.api.shortcuts import detect_base_url
from backend.core.errors import OpenNetWorthError, to_http_status
from backend.db.lunch_flow_config_store import LunchFlowConfigStore
from backend.services.aggregation import lunch_flow as lunch_flow_service
from backend.services.aggregation import lunch_flow_oauth, lunch_flow_pending

router = APIRouter()

# One fixed page for EVERY outcome — mirrors backend/api/mcp_oauth_api.py's own callback page
# byte-for-byte in spirit: no server-, vendor-, or caller-supplied text is ever interpolated,
# so there is no oracle and nothing to reflect. `window.location.origin` (never a caller-
# supplied value) is the explicit postMessage target origin.
_CALLBACK_HTML = """<!doctype html>
<html>
<head><meta charset="utf-8"><title>Open Net Worth</title></head>
<body>
<script>
(function () {
  try {
    if (window.opener) {
      window.opener.postMessage({ source: "onw-lunch-flow-oauth" }, window.location.origin);
    }
  } catch (e) {}
  window.close();
})();
</script>
<p>You can close this window.</p>
</body>
</html>
"""


class LunchFlowConfigUpdate(BaseModel):
    client_id: Optional[str] = None
    # Write-only — blank/omitted keeps the saved secret.
    client_secret: Optional[str] = None
    email: Optional[str] = None
    enabled: Optional[bool] = None


@router.get("/api/lunch-flow/status")
def lunch_flow_status():
    """Public config + sync stats (never the secret / OAuth tokens — only ``has_*`` flags)."""
    return LunchFlowConfigStore().public()


@router.put("/api/lunch-flow/config")
def update_lunch_flow_config(body: LunchFlowConfigUpdate):
    """Configure the connector — developer credentials, pre-fill email, enable/disable. A
    blank ``client_secret`` keeps the saved one (write-only). Returns the public status."""
    store = LunchFlowConfigStore()
    changes: dict = {}
    if body.client_id is not None:
        changes["client_id"] = body.client_id
    if body.client_secret is not None:
        changes["client_secret"] = body.client_secret
    if body.email is not None:
        changes["email"] = body.email
    if body.enabled is not None:
        changes["enabled"] = bool(body.enabled)

    store.update(changes)
    return store.public()


@router.post("/api/lunch-flow/oauth/begin")
def begin_lunch_flow_oauth(request: Request):
    """Start a bank-link sign-in. Always 200 — ``{ok: false, error}`` on any failure,
    matching the MCP OAuth flow's "the request succeeded, the vendor is what didn't answer"
    convention."""
    return lunch_flow_oauth.begin(request_base_url=detect_base_url(request))


@router.get("/api/lunch-flow/oauth/callback")
def lunch_flow_oauth_callback(state: str = "", code: str = "", error: str = ""):
    """The vendor's redirect back to us. Completes the ONE staged flow matching ``state``
    (single-use) and always answers with the SAME fixed page, regardless of outcome — an
    attacker probing ``state`` values can't distinguish "expired" from "never existed" from
    "succeeded" by this response alone.

    Registered BEFORE the ``{flow_id}`` poll route below — Starlette matches routes in
    REGISTRATION order, not by specificity, so a static ``/callback`` segment declared after a
    same-method ``/{flow_id}`` wildcard would never be reached (every GET, including this
    exact one, would match the wildcard first with ``flow_id="callback"``). Order here is
    load-bearing, not cosmetic; ``tests/test_lunch_flow_api.py`` pins both routes' correct
    behavior so a future reordering fails loudly instead of silently swallowing the callback."""
    lunch_flow_oauth.complete(state=state, code=code or None, error=error or None)
    return HTMLResponse(_CALLBACK_HTML, headers={"Cache-Control": "no-store"})


@router.get("/api/lunch-flow/oauth/{flow_id}")
def poll_lunch_flow_oauth(flow_id: str):
    """``{status: pending|ok|error, error}`` — NEVER a token."""
    return lunch_flow_pending.get_status(flow_id)


@router.delete("/api/lunch-flow/connections/{connection_id}")
async def disconnect_lunch_flow_connection(connection_id: int):
    """Unlink one institution. Bad/expired token or a Lunch Flow-side failure → 422. Blocking
    httpx off the loop."""
    try:
        result = await asyncio.to_thread(lunch_flow_service.disconnect_connection, connection_id)
    except OpenNetWorthError as exc:
        raise HTTPException(to_http_status(exc), exc.message or str(exc)) from exc
    return result
