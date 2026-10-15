"""Regression test for I6: `_archive_stale_stories` crashing the whole intel scan.

`intel_agent`'s own monitoring stories carry NAIVE local timestamps
(`datetime.now().isoformat()`), while the Analyst (`monitoring/analyst.py`) writes
tz-AWARE UTC ones (`datetime.now(timezone.utc).isoformat()`) onto the SAME
`intel_stories` table. Comparing an aware cutoff to a naive row's `updated_at`
(or vice versa) raises `TypeError: can't compare offset-naive and offset-aware
datetimes` — not a `ValueError`, so the old `except ValueError` didn't catch it and
the entire scan aborted every run. This pins the fix: both sides are normalized to
aware before comparing, so a mixed-timestamp store can never raise.

IntelStoriesStore is DB-backed; db_isolated redirects it to a throwaway database. No
LLM, no network.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import backend.db.intel_stories_store as iss_mod
from backend.monitoring.intel_agent import STALE_STORY_DAYS, _archive_stale_stories


def _make_store() -> iss_mod.IntelStoriesStore:
    # IntelStoriesStore is DB-backed — the caller's db_isolated fixture redirects the
    # process-wide engine singleton, so a bare construction is isolated.
    return iss_mod.IntelStoriesStore()


def _story(story_id: str, updated_at: str) -> dict:
    return {
        "id": story_id,
        "type": "asset",
        "status": "monitoring",
        "label": story_id,
        "archived": False,
        "created_at": updated_at,
        "updated_at": updated_at,
    }


def test_archive_stale_stories_survives_mixed_naive_and_aware_timestamps(db_isolated):
    stale_days_ago = timedelta(days=STALE_STORY_DAYS + 5)
    fresh_days_ago = timedelta(days=1)

    # Naive local timestamp (how intel_agent itself writes `updated_at`), stale.
    naive_stale = (datetime.now() - stale_days_ago).isoformat()
    # Aware UTC timestamp (how the Analyst writes `updated_at`), stale.
    aware_stale = (datetime.now(timezone.utc) - stale_days_ago).isoformat()
    # Aware UTC timestamp, fresh — must survive.
    aware_fresh = (datetime.now(timezone.utc) - fresh_days_ago).isoformat()

    store = _make_store()
    store.insert(_story("naive-stale", naive_stale))
    store.insert(_story("aware-stale", aware_stale))
    store.insert(_story("aware-fresh", aware_fresh))

    archived = _archive_stale_stories(store)

    assert archived == 2
    by_id = {row["id"]: row for row in store.all()}
    assert by_id["naive-stale"]["archived"] is True
    assert by_id["aware-stale"]["archived"] is True
    assert by_id["aware-fresh"]["archived"] is False
