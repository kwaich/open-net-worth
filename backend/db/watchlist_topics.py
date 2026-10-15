"""Topics are FOLLOWED, not listed (ADR 2026-09-27-topics-are-followed-not-listed).

A watchlist holds tickers; a topic (or company-as-topic) item always has ``watchlist_ids == []``
and is followed once. ``normalize_topics`` brings a payload written before that rule into line:
the Alembic revision ``202609271300_topics_leave_watchlists`` (which keeps its own frozen copy)
for the stored book, and ``investments_io.import_watchlist`` for a restored older export.
"""

from __future__ import annotations

TOPIC_TYPES = frozenset({"topic", "company"})
# The list that only ever existed to hold topics, under each name it has had.
TOPICS_LIST_NAMES = frozenset({"macro & topics", "macro and topics", "intelligence"})
_MERGED_LISTS = ("search_queries", "linked_tickers", "custom_feed_urls")


def is_topic(item: dict) -> bool:
    return item.get("type") in TOPIC_TYPES


def _norm(text: object) -> str:
    return " ".join(str(text or "").split()).casefold()


def _merge_into(keep: dict, dup: dict) -> None:
    for field in _MERGED_LISTS:
        merged = list(keep.get(field) or [])
        merged += [v for v in dup.get(field) or [] if v not in merged]
        if merged:
            keep[field] = merged
    notes = [n for n in (keep.get("note"), dup.get("note")) if n]
    if len(notes) == 2 and _norm(notes[0]) != _norm(notes[1]):
        keep["note"] = f"{notes[0]}\n{notes[1]}"
    elif notes:
        keep["note"] = notes[0]
    if dup.get("monitor_news"):
        keep["monitor_news"] = True


def _dedupe_topics(items: list) -> list:
    """One row per followed topic (case-insensitive value): before the rule, following the same
    topic from two lists made two rows. The first row survives, carrying the others' queries,
    linked tickers, feeds and notes."""
    first: dict[str, dict] = {}
    out: list = []
    for item in items:
        if isinstance(item, dict) and is_topic(item):
            key = _norm(item.get("value"))
            if key and key in first:
                _merge_into(first[key], item)
                continue
            if key:
                first[key] = item
        out.append(item)
    return out


def normalize_topics(payload: object) -> bool:
    """Detach every topic from its lists, merge duplicate topics, and drop the emptied
    topics-only list, in place. True when anything changed; a payload that isn't a dict is
    left alone."""
    if not isinstance(payload, dict):
        return False
    items = payload.get("items") if isinstance(payload.get("items"), list) else []
    lists = payload.get("watchlists") if isinstance(payload.get("watchlists"), list) else []
    changed = False
    for item in items:
        if isinstance(item, dict) and is_topic(item) and item.get("watchlist_ids"):
            item["watchlist_ids"] = []
            changed = True
    deduped = _dedupe_topics(items)
    if len(deduped) != len(items):
        payload["items"] = items = deduped
        changed = True
    in_use = {wid for i in items if isinstance(i, dict) for wid in i.get("watchlist_ids") or []}
    kept = [
        w
        for w in lists
        if not (
            isinstance(w, dict)
            and not w.get("is_auto_sync")
            and _norm(w.get("name")) in TOPICS_LIST_NAMES
            and w.get("id") not in in_use
        )
    ]
    if len(kept) != len(lists):
        payload["watchlists"] = kept
        changed = True
    return changed
