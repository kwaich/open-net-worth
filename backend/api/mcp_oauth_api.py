"""OAuth sign-in for an external MCP server — begin / poll / callback (RFC
``2026-08-14-mcp-oauth`` §4.1/§4.2/§4.3). Thin router: validate → one call → shape.

Three routes, two trust levels:

* ``POST /api/mcp-clients/oauth/begin``   — auth-gated (normal session). Runs the discovery/
  DCR/PKCE sequence and returns an authorize URL to open in a popup, or an honest failure.
* ``GET  /api/mcp-clients/oauth/{flow_id}`` — auth-gated. Polls a staged flow's outcome.
* ``GET  /api/mcp-oauth/callback``        — auth-EXEMPT (``api/_deps.py``'s ``_EXEMPT``). The
  vendor's browser redirect back to us. GET-only, does exactly one thing (complete the ONE
  staged flow matching ``state``), and its response is a fixed, self-contained HTML page —
  no server-, vendor-, or caller-supplied text is ever interpolated into it, so there is no
  oracle and nothing to reflect (RFC §4.3's four hard questions).
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from backend.api.shortcuts import detect_base_url
from backend.mcp import oauth_flow, oauth_pending

router = APIRouter(tags=["mcp-oauth"])

# One fixed page for EVERY outcome — success, a bad `state`, an issuer mismatch, a vendor
# `error=access_denied`, all of it. The real answer lives behind the authenticated poll
# (`GET /api/mcp-clients/oauth/{flow_id}`); this page's only jobs are to nudge the opener
# (via postMessage, the fast path) and close itself. `window.location.origin` — never a
# caller-supplied value — is the explicit postMessage target origin (never `"*"`): it is
# this route's OWN origin, which is exactly the app origin the opener is running at, so no
# interpolation is needed to get an explicit, correct target.
_CALLBACK_HTML = """<!doctype html>
<html>
<head><meta charset="utf-8"><title>Open Net Worth</title></head>
<body>
<script>
(function () {
  try {
    if (window.opener) {
      window.opener.postMessage({ source: "onw-mcp-oauth" }, window.location.origin);
    }
  } catch (e) {}
  window.close();
})();
</script>
<p>You can close this window.</p>
</body>
</html>
"""


class OAuthBeginRequest(BaseModel):
    """``url`` for the add flow (no server exists yet) or ``server_id`` to reconnect an
    existing one — exactly one is meaningful per call. ``client_id`` is the manual escape
    hatch (RFC §4.6's "prompt the user" row) — optional, used only when a prior ``begin``
    answered ``no_registration``."""

    url: Optional[str] = None
    server_id: Optional[str] = None
    client_id: Optional[str] = None


@router.post("/api/mcp-clients/oauth/begin")
def begin_oauth(body: OAuthBeginRequest, request: Request):
    """Start a sign-in. Always 200 — ``{ok: false, reason, error, ...}`` on any failure, matching
    ``probe_mcp_client``'s "the request succeeded, the server is what didn't answer" convention.
    """
    return oauth_flow.begin(
        url=body.url,
        server_id=body.server_id,
        client_id=body.client_id,
        request_base_url=detect_base_url(request),
    )


@router.get("/api/mcp-clients/oauth/{flow_id}")
def poll_oauth(flow_id: str):
    """``{status: pending|ok|error, error}`` — NEVER a token (RFC §4.2 "Learning it finished")."""
    return oauth_pending.get_status(flow_id)


@router.get("/api/mcp-oauth/callback")
def oauth_callback(state: str = "", code: str = "", iss: str = "", error: str = ""):
    """The vendor's redirect back to us. Completes the ONE staged flow matching ``state``
    (single-use — see ``oauth_pending.pop``) and always answers with the SAME fixed page,
    regardless of outcome — an attacker probing ``state`` values can't distinguish "expired"
    from "never existed" from "wrong issuer" from "succeeded" by this response alone."""
    oauth_flow.complete(state=state, code=code or None, iss=iss or None, error=error or None)
    return HTMLResponse(_CALLBACK_HTML, headers={"Cache-Control": "no-store"})
