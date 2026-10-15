"""Regression test for the I6 display-ordering follow-up: `intel_signals._sort_key`
used to compare `updated_at` as a RAW STRING across a population that mixes
naive-local (`intel_agent.py`) and aware-UTC (the Analyst) timestamps — the same
mixed population `_archive_stale_stories` (I6) had to be hardened against, but this
is a DIFFERENT call site: display ordering, not the staleness cutoff.

All I/O isolated to tmp_path. No LLM, no network.
"""

from __future__ import annotations

import pytest

# fixed_local_tz is a shared fixture in tests/conftest.py — see tests/test_timestamps.py
# for why +12h UTC is the offset that makes this reproduce deterministically.


@pytest.fixture
def store(db_isolated):
    """Isolated IntelStoriesStore, wired the same way test_intel_signals_api.py does.
    IntelStoriesStore is DB-backed (intel_stories + signal_timeline_entries, RFC
    intel-signals-data-model) — db_isolated redirects the process-wide engine singleton to
    a fresh, empty database for the test."""
    from backend.db.intel_stories_store import IntelStoriesStore

    return IntelStoriesStore()


def _story(story_id: str, updated_at: str | None, status: str = "monitoring") -> dict:
    row = {
        "id": story_id,
        "type": "asset",
        "status": status,
        "label": story_id,
        "current_summary": "",
        "action_items": [],
        "archived": False,
        "created_at": updated_at or "",
    }
    if updated_at is not None:
        row["updated_at"] = updated_at
    return row


def test_list_signals_orders_mixed_naive_and_aware_by_true_instant(store, fixed_local_tz):
    from backend.services.intel_stories import list_signals

    # Same status band (monitoring) so ordering falls through to the updated_at
    # comparison. At local = UTC+12, "14:00" naive-local is 02:00 UTC — genuinely
    # BEFORE "10:00+00:00" aware — the opposite of what raw string comparison says
    # (see tests/test_timestamps.py::test_raw_string_comparison_is_wrong_for_this_pair).
    store.insert(_story("naive-later-string", "2026-07-07T14:00:00"))
    store.insert(_story("aware-earlier-string", "2026-07-07T10:00:00+00:00"))

    ids = [r["id"] for r in list_signals()]

    # The aware story is newer in absolute time, despite its earlier local clock string.
    assert ids.index("aware-earlier-string") < ids.index("naive-later-string")


def test_list_signals_missing_updated_at_sorts_as_oldest(store):
    from backend.services.intel_stories import list_signals

    store.insert(_story("dated", "2026-07-07T10:00:00+00:00"))
    store.insert(_story("no-timestamp", None))

    ids = [r["id"] for r in list_signals()]
    assert ids == ["dated", "no-timestamp"]
