"""Favicon proxy endpoint — serves a publisher's cached site icon (services.favicons).

Auth-gated like every ``/api`` route: a same-origin ``<img src="/api/favicon?domain=…">`` request
carries the ``onw_session`` cookie, so the app-wide ``require_auth`` gate still applies and this is
NOT an open proxy. Bytes are magic-validated in the service; served with ``nosniff`` + a
locked-down CSP so even a direct navigation can't execute a (rejected-anyway) scriptable image.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response

from backend.core.ratelimit import SlidingWindowLimiter
from backend.services.favicons import get_favicon

router = APIRouter()

# A favicon is immutable for the client's session window; the server cache (services.favicons)
# owns real freshness. nosniff + a 'none' CSP keep a direct hit from being treated as a document.
_IMG_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Content-Security-Policy": "default-src 'none'; sandbox",
    "Cache-Control": "public, max-age=604800, immutable",
}

# Per-client cap: an uncached resolve fans out to several outbound fetches, so throttle to bound
# both abuse (outbound amplification) and load. Generous enough for a full feed render + scrolling.
_LIMITER = SlidingWindowLimiter(max_attempts=240, window_seconds=60.0)


@router.get("/api/favicon")
def favicon(request: Request, domain: str = Query(..., max_length=253)) -> Response:
    """Return the favicon image bytes for ``domain``, or 404 when none can be resolved.

    Sync route (FastAPI threadpools it) so the blocking fetch never stalls the event loop.
    """
    client = request.client.host if request.client else "?"
    allowed, retry_after = _LIMITER.check(client)
    if not allowed:
        raise HTTPException(
            status_code=429, detail="rate limited", headers={"Retry-After": str(retry_after)}
        )
    _LIMITER.record(client)

    icon = get_favicon(domain)
    if icon is None:
        return Response(status_code=404)
    body, content_type = icon
    return Response(content=body, media_type=content_type, headers=_IMG_HEADERS)
