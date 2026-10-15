"""alert_feed — the push-worthy alerts the desktop app raises as native banners.

Fired price alerts, crash-watch fires, explained watch alerts and pushed news analyses live
on Intel stories (``IntelStoriesStore`` — the single in-app surface for them, deliberately
NOT mirrored into the notification log). The desktop reads this feed alongside the log, so
each of those reaches the desktop without a second in-app row and without a notify channel
(a channel's delivery result feeds ``push_retry``'s zero-delivery policy; a channel that
always reported success would silently disable the Telegram/webhook retry).

Every event is DERIVED from what the monitors already write, except a pushed analysis: whether
an analysis is pushed is a decision made after its story is written, so ``record_analysis_push``
stamps the outcome onto the story (``push_log``) at the one place the push happens.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from backend.core.timestamps import sort_key
from backend.db.intel_stories_store import IntelStoriesStore
from backend.services.intel_stories import apply_story_change

WINDOW_HOURS = 24
MAX_EVENTS = 50
PUSH_LOG_FIELD = "push_log"
_PUSH_LOG_KEEP = 5


def record_analysis_push(story_id: str, *, title: str, summary: str) -> None:
    """Stamp a pushed analysis onto its story so the desktop feed can surface it."""
    story = IntelStoriesStore().find_by_id(story_id)
    if story is None:
        return
    entry = {
        "id": uuid.uuid4().hex,
        "at": datetime.now(timezone.utc).isoformat(),
        "title": title,
        "summary": summary,
    }
    log = [*(story.get(PUSH_LOG_FIELD) or []), entry][-_PUSH_LOG_KEEP:]
    apply_story_change({**story, PUSH_LOG_FIELD: log})


def _events_for(story: dict) -> list[dict]:
    story_id = story["id"]
    events: list[dict] = []
    if story.get("type") == "price_alert":
        kind = "crash_watch" if story_id.startswith("crash-watch-") else "price_alert"
        # A re-fire replaces the story outright, so created_at is the fire time — and unlike
        # updated_at it survives the user reading or dismissing the story.
        at = story.get("created_at") or ""
        events.append(
            {
                "id": f"{kind}:{story_id}:{at}",
                "kind": kind,
                "title": story.get("label") or "Price alert",
                "summary": story.get("current_summary") or "",
                "created_at": at,
            }
        )
    explained = story.get("explained")
    if explained and explained.get("what_happened"):
        key = explained.get("trigger_key") or explained.get("at")
        events.append(
            {
                "id": f"watch_alert:{story_id}:{key}",
                "kind": "watch_alert",
                "title": explained["what_happened"],
                "summary": explained.get("why") or "",
                "created_at": explained.get("at") or "",
            }
        )
    for pushed in story.get(PUSH_LOG_FIELD) or []:
        events.append(
            {
                "id": f"analysis:{story_id}:{pushed['id']}",
                "kind": "analysis",
                "title": pushed.get("title") or story.get("label") or "News analysis",
                "summary": pushed.get("summary") or "",
                "created_at": pushed.get("at") or "",
            }
        )
    return events


def recent_alerts(
    *, window_hours: int = WINDOW_HOURS, limit: int = MAX_EVENTS, now: datetime | None = None
) -> list[dict]:
    """Push-worthy alert events from the last ``window_hours``, newest first, at most ``limit``.
    Dismissed, resolved and archived stories are out — the user has already dealt with them."""
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(hours=window_hours)
    events: list[dict] = []
    for story in IntelStoriesStore().all():
        if story.get("dismissed") or story.get("resolved") or story.get("archived"):
            continue
        events.extend(e for e in _events_for(story) if sort_key(e["created_at"]) >= cutoff)
    events.sort(key=lambda e: sort_key(e["created_at"]), reverse=True)
    return events[:limit]
