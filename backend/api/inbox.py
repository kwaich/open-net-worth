"""Unified Inbox — the read-only attention feed (one router over the three
"needs my attention" sources: Intel story arcs, self-harness suggestions, in-app signals).

Thin router: validate query params → call ``services.inbox`` → shape the response.
NO aggregation/normalization logic here (it lives in ``services/inbox.py``). Empty
stores yield ``{items: [], unread_count: 0}`` — never a 500.

**Read-only by design (this slice).** There are deliberately NO write/mutation
endpoints: reviewing or dismissing an item still happens on its source's own surface
(the Intel tab, the self-harness suggestion flow, the signals bell). The Inbox is a
unified *view*, not a second write path. Domain errors map to HTTP via ``core.errors``.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from backend.core.errors import OpenNetWorthError, to_http_status
from backend.services import inbox as inbox_service

router = APIRouter()


@router.get("/api/inbox")
def get_inbox(
    include_read: bool = Query(False),
    limit: Optional[int] = Query(50, ge=1, le=500),
):
    """The unified attention feed + an unread badge count. Actionable + recent items
    first. ``include_read=true`` also surfaces already-handled items; ``limit`` caps
    the merged list. Empty on a fresh instance — never 500."""
    try:
        items = inbox_service.inbox_items(include_read=include_read, limit=limit)
        unread_count = inbox_service.inbox_unread_count()
    except OpenNetWorthError as exc:
        raise HTTPException(to_http_status(exc), exc.message or str(exc)) from exc
    return {"items": items, "unread_count": unread_count}


@router.get("/api/inbox/count")
def get_inbox_count():
    """Just the unread attention count (for a nav badge). Never 500."""
    try:
        unread_count = inbox_service.inbox_unread_count()
    except OpenNetWorthError as exc:
        raise HTTPException(to_http_status(exc), exc.message or str(exc)) from exc
    return {"unread_count": unread_count}
