"""Minimal ASGI objects for tests that call an endpoint FUNCTION directly.

`POST /api/chat` gained a `request: Request` parameter (client-IP demo quotas). FastAPI
fills that from the ASGI scope, so nothing changed for a real request — but ~30 tests call
`chat_mod.chat(req)` as a plain coroutine, bypassing the framework, and those started
failing with `TypeError: chat() missing 1 required positional argument`.

The fix is to hand them a real `Request` rather than to make the parameter optional in
production: an optional `request` would mean the quota path carries a None branch that only
tests ever take, and the tests would stop covering `_client_ip` at all.
"""

from __future__ import annotations

from starlette.requests import Request


def make_request(
    *,
    path: str = "/api/chat",
    method: str = "POST",
    client: tuple[str, int] | None = ("127.0.0.1", 51234),
    headers: dict[str, str] | None = None,
) -> Request:
    """A `Request` with just enough scope for header and client-address reads.

    `client=None` models a request with no peer address (a unix socket, some test
    transports) — `Request.client` is then None, which is the case `_client_ip` has to
    survive.
    """
    raw_headers = [
        (k.lower().encode("latin-1"), v.encode("latin-1")) for k, v in (headers or {}).items()
    ]
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": method,
            "scheme": "http",
            "path": path,
            "raw_path": path.encode(),
            "query_string": b"",
            "root_path": "",
            "headers": raw_headers,
            "client": client,
            "server": ("testserver", 80),
        }
    )
