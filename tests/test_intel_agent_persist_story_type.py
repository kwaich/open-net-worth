"""Regression test for I11: `_persist_story`'s UPDATE branch silently dropping a reclassified
`type`.

The UPDATE branch built `update = {**existing, "label": ..., "status": ..., ...}` and never set
`"type"`, so an existing story's `type` field was frozen at whatever it was created with — even
when the model explicitly reclassified it (e.g. `asset` -> `opportunity`) on a later scan. The
CREATE branch always set `type` correctly; only re-matched (update) stories were affected.

Fix: an update now resolves `effective_type = s.get("type") or existing.get("type") or
default_type or "asset"` — prefers the model's explicit new type, else keeps the existing type,
never silently falls back to the create-only default — and stamps it onto the row. The macro/
`affected_holdings` attachment is decided off the SAME effective type so a reclassification away
from macro can't leave stale computed exposure behind.

IntelStoriesStore is DB-backed; db_isolated redirects it to a throwaway database. No
LLM, no network.
"""

from __future__ import annotations

import backend.db.intel_stories_store as iss_mod
from backend.monitoring.intel_agent import _persist_story


def _make_store() -> iss_mod.IntelStoriesStore:
    # IntelStoriesStore is DB-backed — the caller's db_isolated fixture redirects the
    # process-wide engine singleton, so a bare construction is isolated.
    return iss_mod.IntelStoriesStore()


def _existing_story(story_id: str, story_type: str, **extra) -> dict:
    return {
        "id": story_id,
        "type": story_type,
        "label": "Acme Corp",
        "ticker": "ACME",
        "status": "monitoring",
        "current_summary": "old summary",
        "portfolio_impact": "",
        "action_items": [],
        "affected_holdings": [],
        "archived": False,
        "created_at": "2026-06-01T00:00:00",
        "updated_at": "2026-06-01T00:00:00",
        **extra,
    }


NOW = "2026-07-12T09:00:00"


def test_update_with_explicit_new_type_changes_stored_type(db_isolated):
    """(1) An update that provides a NEW type differing from the existing row's type must
    change the stored type — this is the exact I11 failure mode."""
    store = _make_store()
    store.insert(_existing_story("story-1", "asset"))

    incoming = {
        "id": "story-1",
        "label": "Acme Corp",
        "type": "opportunity",
        "status": "monitoring",
        "summary": "new summary — reclassified as an opportunity",
    }
    _persist_story(store, incoming, url_to_date={}, now=NOW)

    row = store.find_by_id("story-1")
    assert row["type"] == "opportunity"


def test_update_omitting_type_keeps_existing_type(db_isolated):
    """(2) An update that OMITS type must keep the existing row's type — not clobber it to
    the create-only default ("asset")."""
    store = _make_store()
    store.insert(_existing_story("story-2", "opportunity"))

    incoming = {
        "id": "story-2",
        "label": "Acme Corp",
        "status": "monitoring",
        "summary": "a fresh development, no type given",
    }
    _persist_story(store, incoming, url_to_date={}, now=NOW)

    row = store.find_by_id("story-2")
    assert row["type"] == "opportunity"


def test_reclassify_away_from_macro_clears_stale_affected_holdings(db_isolated):
    """(3a) Reclassifying an existing macro story to a non-macro type must not leave stale
    computed `affected_holdings` behind."""
    store = _make_store()
    store.insert(
        _existing_story(
            "story-3",
            "macro",
            affected_holdings=[{"instrument_id": "AAPL", "exposure_pct": 12.5}],
        )
    )

    incoming = {
        "id": "story-3",
        "label": "Acme Corp",
        "type": "asset",
        "status": "monitoring",
        "summary": "no longer a macro story",
    }
    _persist_story(store, incoming, url_to_date={}, now=NOW)

    row = store.find_by_id("story-3")
    assert row["type"] == "asset"
    assert row["affected_holdings"] == []


def test_reclassify_to_macro_attaches_computed_affected_holdings(db_isolated):
    """(3b) Reclassifying an existing non-macro story to macro attaches the freshly computed
    exposure passed by the caller."""
    store = _make_store()
    store.insert(_existing_story("story-4", "asset"))

    incoming = {
        "id": "story-4",
        "label": "Acme Corp",
        "type": "macro",
        "status": "monitoring",
        "summary": "USD strength story",
    }
    computed = [{"instrument_id": "USD_CASH", "exposure_pct": 30.0}]
    _persist_story(store, incoming, url_to_date={}, now=NOW, computed_affected_holdings=computed)

    row = store.find_by_id("story-4")
    assert row["type"] == "macro"
    assert row["affected_holdings"] == computed
