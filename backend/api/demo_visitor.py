"""The per-visitor half of the public-demo guard: who is asking, whose conversation is it, and has
this visitor spent their turns.

``demo_lockdown`` decides which ROUTES a demo refuses. This decides, for the routes it leaves open,
three things that depend on the visitor (ADR
``2026-10-07-the-public-demo-needs-no-password``, amended by
``2026-10-08-a-passwordless-demo-closes-what-the-password-used-to-guard``):

- every request is attributed to a visitor (``bind_visitor``), so a model call made on their
  behalf is charged to their token budget;
- a conversation route answers only for a conversation that visitor owns (``demo_visitors``);
- a deliberate model action — a suggestion, a capture, an approved tool — is a "turn" against the
  visitor's burst window, and a throttled visitor is refused before any provider is reached.
  Reads that merely MAY narrate (a digest, a technicals card) are not turns: they spend through
  the token budgets, which every provider build checks.

Inert unless ``settings.demo_mode`` is on. ``POST /api/chat`` does its own accounting inline,
because it needs the session id it creates.
"""

from __future__ import annotations

import re

from fastapi import HTTPException
from starlette.requests import HTTPConnection
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from backend.services import demo_quota, demo_visitors

_WRITE = frozenset({"POST", "PUT", "PATCH", "DELETE"})

# Routes under /api/sessions/ and /api/chat/ whose first segment is a verb, not a conversation id.
_NOT_A_SESSION = frozenset({"live", "read", "suggestions"})
_SESSION_PATH = re.compile(r"^/api/(?:sessions|chat)/([^/]+)")

# (method, path, which group of the path is the session to bind) — deliberate model actions.
_METERED: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("POST", re.compile(r"^/api/chat/(?P<session>[^/]+)/resolve$")),
    ("POST", re.compile(r"^/api/chat/suggestions$")),
    ("POST", re.compile(r"^/api/capture$")),
    ("POST", re.compile(r"^/api/accounts/describe$")),
    ("POST", re.compile(r"^/api/matches/judge$")),
    ("POST", re.compile(r"^/api/instruments/enrich-(classification|sectors)$")),
    ("POST", re.compile(r"^/api/intel/assets/[^/]+/refresh$")),
)

_NO_CONVERSATION = HTTPException(status_code=404, detail="No such conversation.")


def client_ip(conn: HTTPConnection) -> str:
    """The visitor's network address — see ``demo_visitors.client_key`` for what is trusted."""
    peer = conn.client.host if conn.client is not None else ""
    return demo_visitors.client_key(conn.headers.get("x-forwarded-for") or "", peer)


def visitor_of(conn: HTTPConnection) -> str:
    return demo_visitors.visitor_id(conn.cookies.get(demo_visitors.COOKIE), client_ip(conn))


def metered_session(method: str, path: str) -> tuple[bool, str]:
    """(is this a deliberate model action, the session it belongs to — "" when none)."""
    for verb, pattern in _METERED:
        if verb == method:
            match = pattern.match(path)
            if match:
                return True, match.groupdict().get("session", "")
    return False, ""


async def bind_visitor(conn: HTTPConnection) -> None:
    """App-wide dependency: attribute this request's model calls to its visitor (demo only).

    ``async`` on purpose — a context variable set in a sync dependency (run in a worker thread)
    would not reach the route that makes the call."""
    if demo_quota.enabled():
        demo_quota.bind_visitor(client_ip(conn))


def guard(conn: HTTPConnection) -> None:
    """Scope and meter a request that the route lockdown let through (demo only)."""
    if not demo_quota.enabled():
        return
    method = conn.scope.get("method", "GET")
    path = conn.url.path
    ip = client_ip(conn)

    match = _SESSION_PATH.match(path)
    if match and match.group(1) not in _NOT_A_SESSION:
        if not demo_visitors.may_access(match.group(1), visitor_of(conn), write=method in _WRITE):
            raise _NO_CONVERSATION

    metered, session_id = metered_session(method, path)
    if metered:
        verdict = demo_quota.check(ip)
        if not verdict.allowed:
            raise HTTPException(
                status_code=429,
                detail=verdict.reason,
                headers={"Retry-After": str(verdict.retry_after)},
            )
        demo_quota.record_turn(ip, session_id)


class DemoVisitorMiddleware:
    """Hand a demo visitor their identity cookie on the first API request that lacks one.

    The cookie is also injected into THIS request, so a conversation started on a visitor's very
    first request is owned by the identity the browser will present on every later one."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] != "http"
            or not demo_visitors.enabled()
            or not scope["path"].startswith("/api/")
            or demo_visitors.valid_cookie(HTTPConnection(scope).cookies.get(demo_visitors.COOKIE))
        ):
            await self.app(scope, receive, send)
            return

        value = demo_visitors.new_cookie_value()
        pair = f"{demo_visitors.COOKIE}={value}".encode()
        headers = list(scope["headers"])
        for i, (name, existing) in enumerate(headers):
            if name == b"cookie":
                headers[i] = (name, existing + b"; " + pair)
                break
        else:
            headers.append((b"cookie", pair))
        secure = HTTPConnection(scope).headers.get("x-forwarded-proto") == "https"
        set_cookie = (
            f"{demo_visitors.COOKIE}={value}; Path=/; Max-Age=31536000; HttpOnly; SameSite=Lax"
            + ("; Secure" if secure else "")
        ).encode()

        async def send_with_cookie(message: Message) -> None:
            if message["type"] == "http.response.start":
                message = {
                    **message,
                    "headers": [*message.get("headers", []), (b"set-cookie", set_cookie)],
                }
            await send(message)

        await self.app({**scope, "headers": headers}, receive, send_with_cookie)
