"""Unified Inbox — a read-only aggregator over the app's scattered "needs my
attention" surfaces (overlap audit, "attention surfaces" finding).

Two sources compete for the same job — telling the user what to look at:

  * **Intel** story arcs (``IntelStoriesStore``) — ``status="action_needed"`` arcs
    (and the lower-priority ``"active"`` ones) the intel scan upserts.
  * **In-app notifications** (``NotificationLogStore``) — unread notifications / news /
    macro / opportunity pushes (the in-app ``notify`` channel + the notification-log API).

This service normalizes both into ONE unified item shape and orders them so the
most actionable, most recent things float to the top. It is **compute-only**: it reads
the two stores, never writes, and does no money math (figures already live in each
source's text). Reviewing / dismissing / applying an item still happens on that item's
own ACTION endpoint — delete-intel (``DELETE /api/intel/signals/{id}``) and
dismiss-signal (``POST /api/inbox/signals/{id}/dismiss``). This aggregator is the READ
feed only; it never mutates.

**Never raises.** Each source is read defensively — a failing store degrades to an empty
contribution (logged) instead of 500-ing the whole feed.

Layering: ``services → db → core`` (down only). This module imports the two stores
(``db``) and nothing from ``api``/``ai``.
"""

from __future__ import annotations

from typing import Callable, Optional

from backend.core.logging import get_logger
from backend.core.timestamps import sort_key as _ts_sort_key
from backend.db.intel_stories_store import IntelStoriesStore
from backend.db.notification_log_store import NotificationLogStore

_log = get_logger(__name__)

# Priority bands (lower sorts first). Within a band, newest ``created_at`` wins.
_PRIORITY_TOP = 0  # action_needed intel / new findings / pending suggestions — act on these
_PRIORITY_MID = 1  # everything else (active intel, plain signals)

# How many items each source contributes before the final merge/limit. Generous —
# the final ``limit`` is the real cap; this just bounds an absurd store.
_PER_SOURCE_CAP = 200


def _normalize_intel(signal: dict) -> dict:
    """An intel story-arc signal → a unified inbox item. Intel is never marked
    "read" per-item; an arc is attention-worthy while its status says so, so its
    ``read`` flag tracks status (action_needed = unread, anything else = read)."""
    status = signal.get("status") or "monitoring"
    return {
        "id": f"intel:{signal.get('id', '')}",
        "kind": "intel",
        "title": signal.get("label", "") or "Intel signal",
        "summary": signal.get("current_summary", ""),
        "status": status,
        "created_at": signal.get("updated_at") or signal.get("created_at") or "",
        "source": "intel",
        "proposed_change": None,
        "ref": {"id": signal.get("id", ""), "tab": "intel"},
        "read": status != "action_needed",
        "applicable": False,
    }


_INTEL_SIGNAL_TYPES = {"price_alert", "alert", "news", "macro", "opportunity"}


def _normalize_signal(signal: dict) -> dict:
    """An in-app signal → a unified inbox item. ``read`` is the store's own flag.

    ``ref.tab`` is "intel" only for intel-sourced signal types; a plain notification
    has no source tab ("") so the Inbox renders it without a misleading open-link."""
    sig_type = signal.get("type", "") or "notification"
    return {
        "id": f"signal:{signal.get('id', '')}",
        "kind": "signal",
        "title": signal.get("title", "") or "Notification",
        "summary": signal.get("summary", ""),
        "status": sig_type,
        "created_at": signal.get("created_at", ""),
        "source": sig_type,
        "proposed_change": None,
        "ref": {
            "id": signal.get("id", ""),
            "tab": "intel" if sig_type in _INTEL_SIGNAL_TYPES else "",
        },
        "read": bool(signal.get("read")),
        "applicable": False,
    }


def _priority(item: dict) -> int:
    """Top band = an intel arc that needs action, or an unreviewed agent finding
    (both awaiting a human decision)."""
    if item["kind"] == "intel" and item["status"] == "action_needed":
        return _PRIORITY_TOP
    if item["kind"] == "finding" and item["status"] == "new":
        return _PRIORITY_TOP
    return _PRIORITY_MID


def _safe(label: str, fn: Callable[[], list[dict]]) -> list[dict]:
    """Run one source's read+normalize, degrading a failure to ``[]`` (logged) instead
    of bubbling a 500. The Inbox is a convenience aggregation — one broken store must
    not blank the whole feed."""
    try:
        return fn()
    except Exception:  # noqa: BLE001 — a broken source must degrade, never 500 the feed
        _log.warning("inbox: source %r failed; degrading to empty", label, exc_info=True)
        return []


def inbox_items(
    *,
    include_read: bool = False,
    limit: Optional[int] = 50,
) -> list[dict]:
    """The unified attention feed: Intel ``action_needed``/``active`` arcs + unreviewed
    agent findings + unread in-app signals, normalized to one item shape and ordered
    (actionable + recent first).

    ``include_read=False`` (default) hides already-handled items — read signals,
    reviewed/dismissed findings, and non-``action_needed`` intel arcs. With
    ``include_read=True`` it also surfaces ``active`` intel arcs, all findings, and read
    signals (still ordered by priority then recency). ``limit`` caps the merged result
    (``None`` = no cap).

    Read-only: no writes, no money math. Each item carries a ``ref`` hinting which tab
    opens the source + an ``applicable`` flag. Mutations (review / dismiss / apply) live on
    each source's own ACTION endpoint, never here. **Never raises** — a failing source
    degrades to an empty contribution.
    """
    items: list[dict] = []

    # ── Intel story arcs ────────────────────────────────────────────────────────
    def _intel() -> list[dict]:
        store = IntelStoriesStore()
        statuses = ["action_needed"] + (["active"] if include_read else [])

        # A dismissed / auto-resolved / archived / deleted arc has left the feed — it should
        # never resurface in the attention inbox even while its status still reads action_needed.
        def _live(sig: dict) -> bool:
            return not (
                sig.get("dismissed")
                or sig.get("resolved")
                or sig.get("archived")
                or sig.get("deleted")
            )

        return [
            _normalize_intel(sig) for st in statuses for sig in store.query(status=st) if _live(sig)
        ]

    items += _safe("intel", _intel)

    # ── In-app signals ──────────────────────────────────────────────────────────
    def _signals() -> list[dict]:
        store = NotificationLogStore()
        rows = store.query_signals(unread_only=not include_read, limit=_PER_SOURCE_CAP)
        return [_normalize_signal(s) for s in rows]

    items += _safe("signals", _signals)

    # Order: actionable band first, then newest within a band. Two stable passes —
    # by recency descending, then by priority ascending (Python's sort is stable, so
    # recency order is preserved within each priority band).
    items.sort(key=lambda it: _ts_sort_key(it.get("created_at")), reverse=True)
    items.sort(key=_priority)

    if limit is not None:
        items = items[: max(0, limit)]
    return items


def inbox_unread_count() -> int:
    """Count of unhandled attention items across both sources — backs a nav badge.
    Intel ``action_needed`` arcs + unread in-app signals. Never raises — a failing
    source contributes 0."""
    intel = _safe("intel", lambda: IntelStoriesStore().query(status="action_needed"))
    try:
        signals = NotificationLogStore().unread_count()
    except Exception:  # noqa: BLE001 — degrade a broken store to 0, never 500 the badge
        _log.warning("inbox: signals unread_count failed; counting 0", exc_info=True)
        signals = 0
    return len(intel) + signals
