"""Owned-origin guard with private desktop auth and ordinary browser book auth."""

from __future__ import annotations

import secrets
from dataclasses import dataclass

from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

SESSION_HEADER = "X-ONW-Desktop-Session"


@dataclass(frozen=True, repr=False)
class DesktopSession:
    token: str
    instance_id: str
    port: int

    @property
    def origin(self) -> str:
        return f"http://127.0.0.1:{self.port}"


class DesktopTransportMiddleware:
    def __init__(self, app: ASGIApp, session: DesktopSession):
        self.app = app
        self.session = session

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "lifespan":
            await self.app(scope, receive, send)
            return
        if scope["type"] != "http":
            await send({"type": "websocket.close", "code": 1008})
            return
        headers = Headers(scope=scope)
        expected_host = f"127.0.0.1:{self.session.port}"
        if headers.getlist("host") != [expected_host]:
            await self._deny(scope, receive, send, 403)
            return
        origins = headers.getlist("origin")
        if origins and origins != [self.session.origin]:
            await self._deny(scope, receive, send, 403)
            return
        values = headers.getlist(SESSION_HEADER)
        authenticated = len(values) == 1 and secrets.compare_digest(
            values[0].encode("utf-8"), self.session.token.encode("utf-8")
        )
        if values and not authenticated:
            await self._deny(scope, receive, send, 401)
            return
        scope.setdefault("state", {})["desktop_authenticated"] = authenticated
        scope["state"]["desktop_instance_id"] = self.session.instance_id
        await self.app(scope, receive, send)

    @staticmethod
    async def _deny(scope: Scope, receive: Receive, send: Send, status: int) -> None:
        response = JSONResponse(
            {"detail": "desktop session required" if status == 401 else "invalid desktop origin"},
            status_code=status,
            headers={"Cache-Control": "no-store"},
        )
        await response(scope, receive, send)
