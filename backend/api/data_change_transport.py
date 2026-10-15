"""Fan completed desktop-server REST write responses out to every open client."""

from __future__ import annotations

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from backend.core import data_events


class DataChangeMiddleware:
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] != "http"
            or scope["method"] not in {"POST", "PUT", "PATCH", "DELETE"}
            or not scope["path"].startswith("/api/")
        ):
            await self.app(scope, receive, send)
            return
        status = 500
        published = False

        async def completed(message: Message) -> None:
            nonlocal status, published
            if message["type"] == "http.response.start":
                status = message["status"]
            await send(message)
            if (
                message["type"] == "http.response.body"
                and not message.get("more_body", False)
                and 200 <= status < 300
                and not published
            ):
                published = True
                data_events.publish(["*"])

        await self.app(scope, receive, completed)
