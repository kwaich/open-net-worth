"""Free-text capture endpoint — describe a spend, let Lucius book it.

Thin per the layering rule: validate, rate-limit, call one runner, shape the
response. All turn orchestration lives in ``ai.capture``.
"""

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from backend.ai.capture import (
    MAX_TEXT,
    already_captured,
    failure_message,
    in_flight,
    start_capture,
    wait_for_capture,
)
from backend.core.ratelimit import SlidingWindowLimiter
from backend.core.redact import redact_summary

_log = logging.getLogger(__name__)

router = APIRouter()

# Bound for a single logged payload/reply. Generous enough to read the whole of a
# normal capture, short enough that a runaway client can't flood app.log.
_MAX_LOGGED_BODY = 2000

# Every call here spends model tokens, which the plain ledger POST does not. A
# misfiring automation retrying on a loop is the realistic failure — this bounds
# the bill without getting in the way of a normal shopping day.
_CAPTURE_MAX = 20
_CAPTURE_WINDOW = 60.0
_capture_limiter = SlidingWindowLimiter(_CAPTURE_MAX, _CAPTURE_WINDOW)

# How long the request waits on the turn before answering "still working" and letting it
# finish in the background. Owner-chosen (2026-09-23) to sit just under iOS Shortcuts'
# "Get Contents of URL" timeout (reported ~60s, undocumented), so most turns answer inline
# and the phone still gets a reply to show instead of an error for a capture that did land.
_CAPTURE_WAIT_DEFAULT = 50.0
_CAPTURE_WAIT_MAX = 50.0

_PENDING_REPLY = "Still logging this in the background — you'll get a notification when it's done."


class CaptureRequest(BaseModel):
    text: str
    # Free-form provenance label, echoed into the prompt and the cost surface so
    # captures can be told apart later. Not trusted for any decision.
    source: str = "shortcut"
    # Optional idempotency key, e.g. "tap:2026-07-28T09:15:04". When supplied it
    # is checked against existing external_refs BEFORE the turn runs, so a client
    # that timed out after the write landed can retry without double-booking.
    ref: str = ""
    # Seconds to wait for the turn before answering 202 and finishing in the background.
    # 0 = fire-and-forget. Clamped to [0, _CAPTURE_WAIT_MAX]; None = the default.
    wait: Optional[float] = None


def _client_key(request: Request) -> str:
    """Rate-limit key: client IP only.

    Mirrors ``auth._client_key`` — X-Forwarded-For is deliberately NOT read,
    since a caller could rotate it per request to escape the window.
    """
    client = request.client
    return client.host if client is not None else "unknown"


@router.post("/api/capture")
async def capture(body: CaptureRequest, request: Request):
    """Hand free text to Lucius and let it decide what to log.

    Authenticated by the app-wide ``require_auth`` dependency (Bearer token or
    session cookie) — this path is not in the exempt set.
    """
    # Always logged, both sides: captures arrive from Shortcuts/automations with no
    # client-side trace, so this is the only record of what was sent and what came
    # back. A malformed body never reaches here — that path is logged by the app-wide
    # RequestValidationError handler in ``backend.main``.
    _log.info(
        "capture ← %s: %s",
        _client_key(request),
        redact_summary(body.model_dump(), limit=_MAX_LOGGED_BODY),
    )
    try:
        result = await _run_capture_turn(body, request)
    except HTTPException as exc:
        _log.info("capture → %d: %s", exc.status_code, redact_summary(exc.detail, _MAX_LOGGED_BODY))
        raise
    status = 202 if result.get("pending") else 200
    _log.info("capture → %d: %s", status, redact_summary(result, _MAX_LOGGED_BODY))
    return JSONResponse(result, status_code=status)


async def _run_capture_turn(body: CaptureRequest, request: Request) -> dict:
    """Validate, rate-limit and run the turn. Raises ``HTTPException`` on refusal."""
    text = (body.text or "").strip()
    if not text:
        raise HTTPException(400, "text is required")
    if len(text) > MAX_TEXT:
        raise HTTPException(413, f"text exceeds {MAX_TEXT} characters")

    source = (body.source or "shortcut").strip()[:40] or "shortcut"
    ref = (body.ref or "").strip()[:120]

    # Before the rate limit and before spending a single token: a retry of a
    # capture that already landed is a no-op, not a second purchase.
    if ref and already_captured(source, ref):
        return {
            "ok": True,
            "duplicate": True,
            "reply": "Already logged.",
            "tools_used": [],
            "domains_changed": [],
            "idempotent": True,
            "error": None,
            "pending": False,
        }

    # A retry fired while the first turn is still running joins it rather than booking twice.
    running = in_flight(source, ref)
    if running is not None:
        return await _await_turn(running, body.wait)

    key = _client_key(request)
    allowed, retry_after = _capture_limiter.check(key)
    if not allowed:
        raise HTTPException(
            429,
            f"too many captures; retry in {int(retry_after) + 1}s",
            headers={"Retry-After": str(int(retry_after) + 1)},
        )
    _capture_limiter.record(key)

    return await _await_turn(start_capture(text, source=source, ref=ref or None), body.wait)


async def _await_turn(task, wait: Optional[float]) -> dict:
    """Wait a bounded time for the turn; past it, report it as still running."""
    timeout = _CAPTURE_WAIT_DEFAULT if wait is None else min(max(wait, 0.0), _CAPTURE_WAIT_MAX)
    result = await wait_for_capture(task, timeout)
    if result is None:
        return {
            "ok": True,
            "pending": True,
            "duplicate": False,
            "reply": _PENDING_REPLY,
            "tools_used": [],
            "domains_changed": [],
            "idempotent": False,
            "error": None,
        }
    if not result["ok"]:
        # The turn failed rather than declined — surface it as a server error so
        # the caller can retry, instead of a 200 whose reply is silently empty.
        _log.warning(
            "capture failed after %d attempt(s): %s", result.get("attempts", 1), result["error"]
        )
        raise HTTPException(502, failure_message(result))
    return {**result, "pending": False, "duplicate": False}
