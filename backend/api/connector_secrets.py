"""
GET /api/connectors/{id}/secret/{field} — read back ONE stored connector credential.

The operator-only half of the connector surface. Every other connector payload
(``GET /api/connectors``, the detail view, ``ConnectorView`` anywhere) stays secret-free and
answers ``has_key``/``has_token``; a value is returned ONLY here, only for a field the
form actually renders, and only one field per request. Keeping it off the list/detail
payloads is the point: those are cached by the browser, logged by a proxy and screenshotted,
and a secret that rides along in them is exposed a hundred times to be read once.

## Auth

The app-wide ``require_auth`` dependency (``FastAPI(dependencies=[...])`` in
``backend/main.py``) gates this like every other route; it is deliberately NOT in
``_deps._EXEMPT``. An unauthenticated request never reaches the handler.

## No rate limit — deliberate

Considered and rejected. Rate limiting defends a surface where guessing pays: the login
route is capped at 5 failures / 15 min because an attacker learns something by trying again.
Nothing here is guessable — a caller either presents a valid session/bearer or gets a 401,
and a caller that HAS one can already read the ledger, rewrite the config and rotate these
same credentials through the existing endpoints. A cap would add a real failure mode (a
detail page revealing several fields, or an operator comparing two keys, hitting a 429) to
buy nothing. What ships instead is an audit trail: every reveal is logged with the connector
and field — never the value — so the operator can see reveals in the log viewer.

## Route order is load-bearing

``api/connectors_api.py`` registers ``GET /api/connectors/{connector_id:path}`` and
``:path`` matches slashes, so this router MUST be included before it in ``backend/main.py``
(Starlette matches in registration order) — exactly as ``api/connector_permissions.py`` is.
Pinned by ``tests/test_connector_secrets.py::test_secret_route_wins_over_the_connector_path_route``.

Thin per the layering rule: validate → call ONE service → shape the response.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from backend.core.errors import OpenNetWorthError, to_http_status
from backend.services import connector_secrets

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get(
    "/api/connectors/{connector_id}/secret/{field}",
    include_in_schema=False,  # human-only settings affordance — keep it off the public schema
)
def reveal_connector_secret(connector_id: str, field: str):
    """The saved value of one connector credential, for the logged-in operator.

    404 for an unknown connector, a field that is not revealable, or a field with nothing
    saved (an env-only value counts as nothing saved — see the service docstring).

    ``Cache-Control: no-store`` so the value is never written to a browser or proxy cache.
    """
    try:
        value = connector_secrets.reveal_secret(connector_id, field)
    except OpenNetWorthError as exc:
        raise HTTPException(to_http_status(exc), exc.message or str(exc)) from exc
    # The EVENT, never the value — this is the audit trail the "no rate limit" call rests on.
    logger.info("connector secret revealed: %s/%s", connector_id, field)
    return JSONResponse(
        content={"connector_id": connector_id, "field": field, "value": value},
        headers={"Cache-Control": "no-store"},
    )
