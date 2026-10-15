"""Topics leave watchlists: a watchlist holds tickers; topics are followed, in no list.

ADR 2026-09-27-topics-are-followed-not-listed. Per book:

1. Every topic / company item gets ``watchlist_ids = []`` — it is followed on its own. News
   monitoring never read list membership (it selects topics by type + monitor_news), so what
   is scanned doesn't change.
2. Duplicate topics merge (same value, any case): following one topic from two lists made two
   rows, which would now show twice and stay followed after one "Stop following". The first row
   survives with the others' queries, linked tickers, feeds and notes.
3. The list that existed only to hold topics — a non-auto list named "Macro & topics",
   "Macro and topics" or "Intelligence" — is deleted once it holds no tickers. One that still
   holds tickers is kept as an ordinary list of the user's; if 1200 (since made a no-op) had
   renamed it "Macro & topics", it gets its "Intelligence" name back so it can't be mistaken for
   the Topics & macro tab.

A frozen copy of ``backend.db.watchlist_topics.normalize_topics`` (a migration never imports app
code that may change after it), plus the rename-back.

Downgrade can't reconstruct which list each topic was filed in, so it leaves the data as is;
the earlier code reads ``[]`` fine (it only refused it on write).

Revision ID: 202609271300_topics_leave_watchlists
Revises: 202609271200_rename_intelligence_watchlist
Create Date: 2026-09-27
"""

from __future__ import annotations

import json

import sqlalchemy as sa
from alembic import op

revision = "202609271300_topics_leave_watchlists"
down_revision = "202609271200_rename_intelligence_watchlist"
branch_labels = None
depends_on = None

_TOPIC_TYPES = {"topic", "company"}
_TOPICS_LIST_NAMES = {"macro & topics", "macro and topics", "intelligence"}
_RENAMED = {"macro & topics", "macro and topics"}
_OLD_NAME = "Intelligence"
_MERGED_LISTS = ("search_queries", "linked_tickers", "custom_feed_urls")


def _norm(text: object) -> str:
    return " ".join(str(text or "").split()).casefold()


def _is_topic(item: object) -> bool:
    return isinstance(item, dict) and item.get("type") in _TOPIC_TYPES


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


def _dedupe(items: list) -> list:
    first: dict[str, dict] = {}
    out: list = []
    for item in items:
        if _is_topic(item):
            key = _norm(item.get("value"))
            if key and key in first:
                _merge_into(first[key], item)
                continue
            if key:
                first[key] = item
        out.append(item)
    return out


def _rename_back(lists: list) -> bool:
    """A kept ticker list 1200 had renamed "Macro & topics" is "Intelligence" again."""
    if any(isinstance(w, dict) and _norm(w.get("name")) == "intelligence" for w in lists):
        return False
    for w in lists:
        if isinstance(w, dict) and not w.get("is_auto_sync") and _norm(w.get("name")) in _RENAMED:
            w["name"] = _OLD_NAME
            return True
    return False


def migrated(payload: object) -> bool:
    """Detach and merge topics, drop the emptied topics list, in place; True when changed."""
    if not isinstance(payload, dict):
        return False
    items = payload.get("items") if isinstance(payload.get("items"), list) else []
    lists = payload.get("watchlists") if isinstance(payload.get("watchlists"), list) else []
    changed = False
    for item in items:
        if _is_topic(item) and item.get("watchlist_ids"):
            item["watchlist_ids"] = []
            changed = True
    deduped = _dedupe(items)
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
            and _norm(w.get("name")) in _TOPICS_LIST_NAMES
            and w.get("id") not in in_use
        )
    ]
    if len(kept) != len(lists):
        payload["watchlists"] = kept
        changed = True
    return _rename_back(kept) or changed


def upgrade() -> None:
    conn = op.get_bind()
    rows = conn.execute(sa.text("SELECT book_id, payload FROM watchlists")).fetchall()
    for book_id, raw in rows:
        try:
            payload = json.loads(raw) if isinstance(raw, str) else raw
        except ValueError:
            continue  # unreadable is left for the store's own shape repair, never a boot blocker
        if not migrated(payload):
            continue
        conn.execute(
            sa.text("UPDATE watchlists SET payload = :payload WHERE book_id = :book_id"),
            {"book_id": book_id, "payload": json.dumps(payload)},
        )


def downgrade() -> None:
    pass
