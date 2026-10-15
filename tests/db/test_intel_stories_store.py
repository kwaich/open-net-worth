"""IntelStoriesStore + DailyDigestStore — DB-CRUD round trip (rename/rewrite of the old
``tests/db/test_intel_signals_store.py``, RFC intel-signals-data-model). Every row here IS a
story now (the old multi-shape ``intel_signals`` table's ``is_story_row`` filter has no
successor to test — `daily_digest` rows physically cannot land in `intel_stories` any more).
"""

from __future__ import annotations

from backend.db.daily_digest_store import DailyDigestStore
from backend.db.intel_stories_store import IntelStoriesStore


def _story(story_id: str = "story_1", **overrides) -> dict:
    base = {
        "id": story_id,
        "type": "asset",
        "instrument_id": "inst_abc123",
        "ticker": "AAPL",
        "label": "AAPL supply chain",
        "status": "action_needed",
        "current_summary": "Supply chain risk building",
        "portfolio_impact": "",
        "action_items": [],
        "analyzed": True,
        "read": False,
        "dismissed": False,
        "resolved": False,
        "archived": False,
        "created_at": "2026-08-01T00:00:00Z",
        "updated_at": "2026-08-01T00:00:00Z",
    }
    base.update(overrides)
    return base


def _entry(entry_id: str = "e1", **overrides) -> dict:
    base = {
        "id": entry_id,
        "date": "2026-08-01T00:00:00Z",
        "title": "New development",
        "summary": "Something happened",
        "sources": [{"url": "https://example.com", "title": "Example"}],
        "superseded": False,
    }
    base.update(overrides)
    return base


# ── Story CRUD ──────────────────────────────────────────────────────────────────────


def test_insert_and_find_by_id(db_engine):
    store = IntelStoriesStore(engine=db_engine)
    store.insert(_story())
    found = store.find_by_id("story_1")
    assert found["label"] == "AAPL supply chain"
    assert found["status"] == "action_needed"


def test_all_returns_insertion_order(db_engine):
    store = IntelStoriesStore(engine=db_engine)
    store.insert(_story("s1"))
    store.insert(_story("s2"))
    assert [s["id"] for s in store.all()] == ["s1", "s2"]


def test_query_filters_by_field(db_engine):
    store = IntelStoriesStore(engine=db_engine)
    store.insert(_story("s1", status="action_needed"))
    store.insert(_story("s2", status="monitoring"))
    assert [s["id"] for s in store.query(status="action_needed")] == ["s1"]


def test_update_by_id_merges_and_persists(db_engine):
    store = IntelStoriesStore(engine=db_engine)
    store.insert(_story())
    updated = store.update_by_id("story_1", {"status": "resolved", "resolved": True})
    assert updated["status"] == "resolved"
    assert store.find_by_id("story_1")["status"] == "resolved"
    # Untouched fields survive the merge.
    assert store.find_by_id("story_1")["label"] == "AAPL supply chain"


def test_update_by_id_missing_returns_none(db_engine):
    store = IntelStoriesStore(engine=db_engine)
    assert store.update_by_id("nope", {"status": "resolved"}) is None


def test_upsert_inserts_new_and_replaces_existing(db_engine):
    store = IntelStoriesStore(engine=db_engine)
    store.upsert(_story())
    assert store.find_by_id("story_1")["status"] == "action_needed"
    store.upsert(_story(status="monitoring"))
    assert store.find_by_id("story_1")["status"] == "monitoring"
    assert len(store.all()) == 1


def test_delete_removes_row(db_engine):
    store = IntelStoriesStore(engine=db_engine)
    store.insert(_story())
    assert store.delete("story_1") is True
    assert store.find_by_id("story_1") is None
    assert store.delete("story_1") is False


def test_topic_row_has_null_instrument_id(db_engine):
    """A topic row genuinely has no single instrument — RFC §2.1: NULL, not a guess."""
    store = IntelStoriesStore(engine=db_engine)
    store.insert(_story("topic_1", type="topic", instrument_id=None, ticker=None))
    assert store.find_by_id("topic_1")["instrument_id"] is None


# ── Timeline entries (owned directly on IntelStoriesStore) ──────────────────────────


def test_append_timeline_entry_and_read_back(db_engine):
    store = IntelStoriesStore(engine=db_engine)
    store.insert(_story())
    store.append_timeline_entry("story_1", _entry("e1", summary="First development"))
    entries = store.entries_for_signal("story_1")
    assert len(entries) == 1
    assert entries[0]["summary"] == "First development"


def test_entries_for_signal_newest_first(db_engine):
    """Matches the old prepend-ordered `timeline[]` wire shape — StoryArcCard.tsx and
    ai/tools/asset_context.py's `_timeline_entry_view` both expect newest-first."""
    store = IntelStoriesStore(engine=db_engine)
    store.insert(_story())
    store.append_timeline_entry("story_1", _entry("e1", date="2026-08-01T00:00:00Z"))
    store.append_timeline_entry("story_1", _entry("e2", date="2026-08-02T00:00:00Z"))
    store.append_timeline_entry("story_1", _entry("e3", date="2026-08-03T00:00:00Z"))
    assert [e["id"] for e in store.entries_for_signal("story_1")] == ["e3", "e2", "e1"]


def test_entries_for_signal_limit_caps_to_most_recent(db_engine):
    store = IntelStoriesStore(engine=db_engine)
    store.insert(_story())
    for i in range(25):
        store.append_timeline_entry(
            "story_1", _entry(f"e{i}", date=f"2026-08-{i + 1:02d}T00:00:00Z")
        )
    bounded = store.entries_for_signal("story_1", limit=20)
    assert len(bounded) == 20
    assert store.count_entries_for_signal("story_1") == 25
    # Most recent 20 — e24 down to e5 (25 entries, 0-indexed, newest-first).
    assert bounded[0]["id"] == "e24"
    assert bounded[-1]["id"] == "e5"


def test_entries_scoped_per_signal(db_engine):
    store = IntelStoriesStore(engine=db_engine)
    store.insert(_story("s1"))
    store.insert(_story("s2"))
    store.append_timeline_entry("s1", _entry("e1"))
    store.append_timeline_entry("s2", _entry("e2"))
    assert [e["id"] for e in store.entries_for_signal("s1")] == ["e1"]
    assert [e["id"] for e in store.entries_for_signal("s2")] == ["e2"]


def test_delete_story_cascades_to_entries(db_engine):
    store = IntelStoriesStore(engine=db_engine)
    store.insert(_story())
    store.append_timeline_entry("story_1", _entry("e1"))
    store.delete("story_1")
    assert store.entries_for_signal("story_1") == []


def test_mark_entry_superseded_flips_flag_scoped_to_signal(db_engine):
    store = IntelStoriesStore(engine=db_engine)
    store.insert(_story("s1"))
    store.insert(_story("s2"))
    store.append_timeline_entry("s1", _entry("e1"))
    assert store.mark_entry_superseded("s1", "e1") is True
    entries = store.entries_for_signal("s1")
    assert entries[0]["superseded"] is True
    # Scoped: the same entry id under a DIFFERENT signal is untouched (never happens in
    # practice — ids are uuid4().hex — but the lookup itself must stay signal-scoped).
    assert store.mark_entry_superseded("s2", "e1") is False


def test_touch_entry_stamps_as_of_scoped_to_signal(db_engine):
    store = IntelStoriesStore(engine=db_engine)
    store.insert(_story("s1"))
    store.insert(_story("s2"))
    store.append_timeline_entry("s1", _entry("e1"))
    assert store.touch_entry("s1", "e1") is True
    entries = store.entries_for_signal("s1")
    assert entries[0]["as_of"]
    # Other fields untouched by a touch.
    assert entries[0]["summary"] == "Something happened"
    # Scoped: same entry id under a DIFFERENT signal is untouched.
    assert store.touch_entry("s2", "e1") is False


def test_touch_entry_missing_returns_false(db_engine):
    store = IntelStoriesStore(engine=db_engine)
    store.insert(_story())
    assert store.touch_entry("story_1", "no-such-entry") is False


# ── find_story_by_ticker / find_story_by_label ───────────────────────────────────────


def test_find_story_by_ticker_case_insensitive(db_engine):
    store = IntelStoriesStore(engine=db_engine)
    store.insert(_story("s1", ticker="AAPL"))
    assert store.find_story_by_ticker("aapl")["id"] == "s1"
    assert store.find_story_by_ticker("MSFT") is None


def test_find_story_by_label_case_insensitive(db_engine):
    store = IntelStoriesStore(engine=db_engine)
    store.insert(_story("s1", label="AAPL Supply Chain"))
    assert store.find_story_by_label("aapl supply chain")["id"] == "s1"
    assert store.find_story_by_label("no such label") is None


def test_signal_timeline_entries_is_checkpoint_included(db_isolated):
    """New DB-assigned-PK (seq, Integer) child table stays covered by /rewind, same as
    its parent — proves the int-pk-via-str-cast checkpoint mechanism actually round-trips,
    not just that it doesn't crash."""
    from backend.ai import checkpoint as cp
    from backend.ai.checkpoint import _EXCLUDED_TABLES

    assert "intel_stories" not in _EXCLUDED_TABLES
    assert "signal_timeline_entries" not in _EXCLUDED_TABLES

    store = IntelStoriesStore()
    store.insert(_story())
    ctx, token = cp.begin_turn("s1", 0, "record a development")
    try:
        store.append_timeline_entry("story_1", _entry("e1"))
    finally:
        entry = cp.end_turn(ctx, token)
    assert entry is not None
    assert "signal_timeline_entries" in entry["db_tables"]

    cp.restore(entry["id"], mode="data")
    assert store.entries_for_signal("story_1") == []


# ── DailyDigestStore ──────────────────────────────────────────────────────────────────


def _digest(date: str = "2026-08-01", **overrides) -> dict:
    base = {
        "date": date,
        "created_at": "2026-08-01T09:00:00Z",
        "trigger": "premarket",
        "severity": "info",
        "title": "Daily Digest",
        "message": "Portfolio up 1.2% today.",
        "affected_tickers": [],
        "sources": [],
        "source_urls": [],
        "read": False,
    }
    base.update(overrides)
    return base


def test_daily_digest_upsert_and_get(db_engine):
    store = DailyDigestStore(engine=db_engine)
    store.upsert(_digest())
    got = store.get("2026-08-01")
    assert got["message"] == "Portfolio up 1.2% today."
    assert got["trigger"] == "premarket"


def test_daily_digest_upsert_same_date_overwrites(db_engine):
    """Preserves the pre-existing same-day EOD-overwrites-premarket behavior (RFC §2.3):
    `date` is a literal PK, so a second write for the same date replaces the first."""
    store = DailyDigestStore(engine=db_engine)
    store.upsert(_digest(trigger="premarket", message="Premarket digest"))
    store.upsert(_digest(trigger="eod", message="EOD digest"))
    got = store.get("2026-08-01")
    assert got["trigger"] == "eod"
    assert got["message"] == "EOD digest"
    assert len(store.all()) == 1


def test_daily_digest_get_missing_returns_none(db_engine):
    store = DailyDigestStore(engine=db_engine)
    assert store.get("2026-01-01") is None


def test_daily_digest_delete(db_engine):
    store = DailyDigestStore(engine=db_engine)
    store.upsert(_digest())
    assert store.delete("2026-08-01") is True
    assert store.get("2026-08-01") is None
    assert store.delete("2026-08-01") is False


def test_daily_digests_is_checkpoint_included(db_isolated):
    """Deliberate policy: daily_digests is wipe-only/NOT bridged (portable export), but
    checkpoint inclusion is UNCHANGED from its old home inside intel_signals."""
    from backend.ai.checkpoint import _EXCLUDED_TABLES

    assert "daily_digests" not in _EXCLUDED_TABLES
