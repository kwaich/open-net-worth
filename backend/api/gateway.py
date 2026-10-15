"""Gateway mode — run behind a trusted authenticating reverse proxy.

With ``ONW_GATEWAY_TOKEN`` set, the proxy has already signed the user in and is the only
authority: EVERY request (HTTP, SSE, websocket, the static SPA) must carry the shared token in
``X-Onw-Gateway-Token``, except ``GET /api/health`` for health checks. A valid token marks the
request ``gateway_authenticated`` so ``require_auth`` and ``/api/auth/status`` treat it as
signed in. ``X-Onw-User-Email`` and ``X-Onw-Portal-Url`` are display-only — never an
authorization input — and, like ``X-Onw-Session-Started`` (the unix time the proxy minted the
signed-in session, read by the terminal's freshness check), are only believed on a request the
proxy marked ``session``; a pass-through request's copies are dropped.

The proxy also stamps every forwarded request with ``X-Onw-Gateway-Auth``: ``session`` when it
has signed the owner in, ``none`` when it has not (a phone Shortcut or MCP client carrying its
own API token, a QR download ticket). Only a single, exact ``session`` grants
``gateway_authenticated``. Anything else — ``none``, missing, garbage, duplicated — fails closed
to ``gateway_passthrough``: the request is valid-token but unauthenticated, and ``require_auth``
then demands the app's own credential and treats it as remote whatever its TCP peer is.

Pure ASGI and installed outermost, so it runs before routing and the static mounts (which
bypass app-level dependencies). With the variable unset it is a pass-through.
"""

from __future__ import annotations

import hmac
from urllib.parse import urlsplit

from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from backend.core.config import get_settings

TOKEN_HEADER = "x-onw-gateway-token"
EMAIL_HEADER = "x-onw-user-email"
AUTH_HEADER = "x-onw-gateway-auth"
PORTAL_URL_HEADER = "x-onw-portal-url"
SESSION_STARTED_HEADER = "x-onw-session-started"
_SESSION_VALUE = "session"
_HEALTH_PATH = "/api/health"
_MAX_EMAIL_LEN = 320
_MAX_URL_LEN = 2048


def _portal_url(raw: str | None) -> str | None:
    """The portal link to show, or None. Only an absolute http(s) URL is a link — anything else
    (``javascript:``, relative, garbage) would be a script or phishing vector in an ``<a href>``."""
    value = (raw or "").strip()
    if not value or len(value) > _MAX_URL_LEN:
        return None
    parts = urlsplit(value)
    if parts.scheme not in ("http", "https") or not parts.netloc or parts.username:
        return None
    return value


def _session_started(raw: str | None) -> int | None:
    try:
        value = int((raw or "").strip())
    except ValueError:
        return None
    return value if value > 0 else None


def token_matches(presented: str, expected: str) -> bool:
    return hmac.compare_digest(presented.encode("utf-8"), expected.encode("utf-8"))


class GatewayTokenMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        expected = get_settings().gateway_token
        if expected is None or scope["type"] == "lifespan":
            await self.app(scope, receive, send)
            return
        if scope["type"] == "http" and scope["method"] == "GET" and scope["path"] == _HEALTH_PATH:
            await self.app(scope, receive, send)
            return
        headers = Headers(scope=scope)
        presented = headers.get(TOKEN_HEADER)
        if presented is None or not token_matches(presented, expected):
            await self._deny(scope, receive, send)
            return
        state = scope.setdefault("state", {})
        if headers.getlist(AUTH_HEADER) == [_SESSION_VALUE]:
            state["gateway_authenticated"] = True
            email = (headers.get(EMAIL_HEADER) or "").strip()
            state["gateway_email"] = email[:_MAX_EMAIL_LEN] or None
            state["gateway_portal_url"] = _portal_url(headers.get(PORTAL_URL_HEADER))
            state["gateway_session_started"] = _session_started(headers.get(SESSION_STARTED_HEADER))
        else:
            state["gateway_passthrough"] = True
        await self.app(scope, receive, send)

    @staticmethod
    async def _deny(scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return
        response = JSONResponse(
            {"detail": "gateway token required"},
            status_code=403,
            headers={"Cache-Control": "no-store"},
        )
        await response(scope, receive, send)
