"""The in-browser terminal.

GET /api/terminal/status  — can this browser open a terminal right now, and if not why
WS  /api/terminal         — a login shell on a pty (``services.terminal`` holds every gate)

Wire format: the browser sends JSON text frames — ``{"type": "input", "data": "..."}`` and
``{"type": "resize", "cols": N, "rows": M}``; the server sends the shell's output as binary frames
and a final ``{"type": "exit", "code": N}`` text frame when it ends. Close codes: 4401 not signed
in (or signed in too long ago), 4403 wrong origin, 4404 not available here, 4408 idle timeout,
4409 a terminal is already open.

SECURITY: this is a shell. ``/security-review`` before changing any gate; see the service.
"""

from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter, WebSocket
from fastapi.responses import JSONResponse
from starlette.requests import HTTPConnection
from starlette.websockets import WebSocketDisconnect

from backend.core.errors import ConflictError
from backend.core.pty_session import PtySession
from backend.services import terminal

logger = logging.getLogger(__name__)

router = APIRouter()

_DEFAULT_SIZE = (80, 24)
_CLOSE_UNAUTHENTICATED = 4401
_CLOSE_ORIGIN = 4403
_CLOSE_UNAVAILABLE = 4404
_CLOSE_IDLE = 4408
_CLOSE_BUSY = 4409
_FRESH_SESSION_CODE = "fresh_session_required"


def _stale_session_response() -> JSONResponse:
    return JSONResponse(
        {
            "detail": "sign in again to use the terminal",
            "code": _FRESH_SESSION_CODE,
        },
        status_code=401,
    )


@router.get("/api/terminal/status")
def terminal_status(conn: HTTPConnection):
    reason = terminal.unavailable_reason()
    if reason is None and not terminal.is_signed_in(conn):
        reason = "not_signed_in"
    if reason is None and not terminal.is_fresh(conn):
        return _stale_session_response()
    return {"available": reason is None, "reason": reason, "in_use": terminal.in_use()}


def _refusal(ws: WebSocket) -> tuple[int, str, bool] | None:
    """``(close code, reason, accept first)`` when this handshake must be refused. A refusal
    that is not about who is asking is delivered over an accepted socket so the page can read
    its code; origin and identity failures get a bare handshake rejection."""
    if terminal.unavailable_reason() is not None:
        return _CLOSE_UNAVAILABLE, "terminal is not available", False
    if not terminal.same_origin(ws):
        return _CLOSE_ORIGIN, "cross-origin connection refused", False
    if not terminal.is_signed_in(ws):
        return _CLOSE_UNAUTHENTICATED, "not signed in", False
    if not terminal.is_fresh(ws):
        return _CLOSE_UNAUTHENTICATED, _FRESH_SESSION_CODE, True
    return None


async def _pump_output(ws: WebSocket, session: PtySession) -> None:
    while (chunk := await session.read()) is not None:
        await ws.send_bytes(chunk)
    code = await asyncio.to_thread(lambda: session.exit_code())
    await ws.send_text(json.dumps({"type": "exit", "code": code}))


async def _pump_input(ws: WebSocket, session: PtySession) -> int:
    """Forward the browser's keystrokes and resizes; returns the close code to end with."""
    while True:
        try:
            message = await asyncio.wait_for(ws.receive(), terminal.IDLE_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            return _CLOSE_IDLE
        if message["type"] == "websocket.disconnect":
            return 1000
        text = message.get("text")
        if text is None:
            continue
        if len(text) > terminal.MAX_INPUT_BYTES:
            return 1009
        _apply(session, text)


def _apply(session: PtySession, text: str) -> None:
    try:
        event = json.loads(text)
    except ValueError:
        return
    if not isinstance(event, dict):
        return
    if event.get("type") == "input" and isinstance(event.get("data"), str):
        session.write(event["data"].encode("utf-8"))
    elif event.get("type") == "resize":
        size = terminal.clamp_size(event.get("cols"), event.get("rows"))
        if size:
            session.resize(*size)


@router.websocket("/api/terminal")
async def terminal_socket(ws: WebSocket) -> None:
    refusal = _refusal(ws)
    if refusal:
        code, reason, accept_first = refusal
        if accept_first:
            await ws.accept()
        await ws.close(code=code, reason=reason)
        return
    try:
        terminal.acquire()
    except ConflictError:
        await ws.accept()
        await ws.close(code=_CLOSE_BUSY, reason="a terminal is already open")
        return
    session: PtySession | None = None
    try:
        session = terminal.open_shell(*_DEFAULT_SIZE)
        terminal.record_opened(terminal.actor(ws))
        await ws.accept()
        await _run(ws, session)
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("terminal session failed")
    finally:
        terminal.release()
        if session is not None:
            await session.close()


async def _run(ws: WebSocket, session: PtySession) -> None:
    output = asyncio.create_task(_pump_output(ws, session))
    keys = asyncio.create_task(_pump_input(ws, session))
    _, pending = await asyncio.wait({output, keys}, return_when=asyncio.FIRST_COMPLETED)
    for task in pending:
        task.cancel()
    # Collect both so a pump that died on a closed socket is never reported as unretrieved.
    _, keys_result = await asyncio.gather(output, keys, return_exceptions=True)
    code = keys_result if isinstance(keys_result, int) else 1000
    try:
        await ws.close(code=code)
    except RuntimeError:
        pass
