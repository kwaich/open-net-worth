"""Regression test for I9: `_existing_stories_block` crashing the whole intel scan.

Historically `IntelSignalsStore` was a MULTI-WRITER table — alongside `intel_agent`'s own
story rows it also held the daily-digest row (`monitoring/digest.py::_write_inapp_digest`,
no `label`/no `status`), and the old block hard-indexed `s['label']`/`s['status']` over
every row, so the first digest row raised `KeyError: 'label'` and aborted the whole scan.

The schema split (RFC intel-signals-data-model) moved `daily_digest` rows to their own
`daily_digests` table — `intel_stories` can structurally never hold a non-story row anymore,
so I9's original mixed-shape crash cannot recur. This narrows to the surviving regression:
the block still renders correctly over genuine story rows from multiple writers (intel_agent,
the Analyst).

IntelStoriesStore is DB-backed; db_isolated redirects it to a throwaway database. No
LLM, no network.
"""

from __future__ import annotations

from datetime import datetime

import backend.db.intel_stories_store as iss_mod
from backend.monitoring.intel_agent import _existing_stories_block


def _make_store() -> iss_mod.IntelStoriesStore:
    # IntelStoriesStore is DB-backed — the caller's db_isolated fixture redirects the
    # process-wide engine singleton, so a bare construction is isolated.
    return iss_mod.IntelStoriesStore()


def _intel_story_row() -> dict:
    """A normal intel_agent-authored story row."""
    now = datetime.now().isoformat()
    return {
        "id": "story-1",
        "type": "asset",
        "label": "Acme Corp",
        "ticker": "ACME",
        "status": "monitoring",
        "current_summary": "Acme reported strong earnings.",
        "archived": False,
        "created_at": now,
        "updated_at": now,
    }


def _analyst_news_row() -> dict:
    """Shaped like analyst.py's _create_news_spike_signal output — a second writer's story."""
    now = datetime.now().isoformat()
    return {
        "id": "news-acme",
        "type": "asset",
        "label": "Acme Corp",
        "ticker": "ACME",
        "status": "monitoring",
        "current_summary": "News spike analyzed.",
        "analyzed": True,
        "created_at": now,
        "updated_at": now,
    }


def test_existing_stories_block_renders_every_genuine_story(db_isolated):
    store = _make_store()
    store.insert(_intel_story_row())
    store.insert(_analyst_news_row())

    rows = [s for s in store.all() if not s.get("archived")]
    block = _existing_stories_block(rows)

    assert "id=story-1" in block
    assert "Acme Corp" in block
    assert "id=news-acme" in block
    assert "summary: Acme reported strong earnings." in block
