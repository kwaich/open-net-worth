"""
API tests for the intel signal lifecycle:
  GET    /api/intel/signals (?include_archived)   — feed vs feed+archive
  PATCH  /api/intel/signals/{id}                  — read / dismiss / restore / resolve
  POST   /api/intel/signals/read-all              — bulk mark read
  DELETE /api/intel/signals/{id}                  — permanent hard delete
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.main import app


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def store(db_isolated):
    """Isolated IntelStoriesStore, pre-seeded with a feed + an archive item. IntelStoriesStore
    is DB-backed (intel_stories + signal_timeline_entries, RFC intel-signals-data-model) —
    db_isolated redirects the process-wide engine singleton to a fresh, empty database for
    the test."""
    from backend.db.intel_stories_store import IntelStoriesStore

    s = IntelStoriesStore()
    base = {"type": "asset", "label": "X", "current_summary": "", "action_items": []}
    s.insert({**base, "id": "feed-1", "status": "action_needed", "label": "Feed one"})
    s.insert({**base, "id": "feed-2", "status": "monitoring", "label": "Feed two"})
    s.insert(
        {**base, "id": "arch-1", "status": "monitoring", "label": "Dismissed", "dismissed": True}
    )
    s.insert(
        {
            **base,
            "id": "res-1",
            "status": "monitoring",
            "label": "Resolved",
            "resolved": True,
            "resolved_reason": "cleared",
        }
    )
    return s


def _ids(rows):
    return {r["id"] for r in rows}


def test_get_feed_excludes_archived(client, store):
    rows = client.get("/api/intel/signals").json()
    assert _ids(rows) == {"feed-1", "feed-2"}


def test_get_include_archived_returns_all(client, store):
    rows = client.get("/api/intel/signals?include_archived=true").json()
    assert _ids(rows) == {"feed-1", "feed-2", "arch-1", "res-1"}


def test_patch_mark_read(client, store):
    r = client.patch("/api/intel/signals/feed-1", json={"read": True})
    assert r.status_code == 200
    assert r.json()["read"] is True
    assert store.find_by_id("feed-1")["read"] is True


def test_patch_dismiss_leaves_feed_and_marks_read(client, store):
    r = client.patch("/api/intel/signals/feed-1", json={"dismissed": True})
    assert r.status_code == 200
    body = r.json()
    assert body["dismissed"] is True and body["read"] is True
    # gone from the feed, present in the archive
    assert "feed-1" not in _ids(client.get("/api/intel/signals").json())
    assert "feed-1" in _ids(client.get("/api/intel/signals?include_archived=true").json())


def test_patch_restore_returns_to_feed(client, store):
    client.patch("/api/intel/signals/arch-1", json={"dismissed": False})
    rows = client.get("/api/intel/signals").json()
    assert "arch-1" in _ids(rows)
    restored = store.find_by_id("arch-1")
    assert restored["dismissed"] is False and restored["resolved"] is False


def test_patch_reopen_resolved(client, store):
    client.patch("/api/intel/signals/res-1", json={"resolved": False})
    assert "res-1" in _ids(client.get("/api/intel/signals").json())


def test_patch_unknown_id_404(client, store):
    assert client.patch("/api/intel/signals/nope", json={"read": True}).status_code == 404


def test_read_all_marks_feed(client, store):
    out = client.post("/api/intel/signals/read-all").json()
    assert out["updated"] == 2  # only the two live-feed signals
    assert store.find_by_id("feed-1")["read"] is True
    assert store.find_by_id("feed-2")["read"] is True
    # archived items are not touched
    assert not store.find_by_id("arch-1").get("read")


def test_delete_is_permanent(client, store):
    assert client.delete("/api/intel/signals/feed-1").json()["ok"] is True
    assert store.find_by_id("feed-1") is None
    assert "feed-1" not in _ids(client.get("/api/intel/signals?include_archived=true").json())


# ── F11 fix (owner-reported, 2026-08-29): storage keeps a signal's FULL timeline forever now
# (no more silent eviction past a hard cap) — but the LIST feed still embeds a BOUNDED window
# per signal (most-recent-N + showing/total), and a new single-signal GET returns the full,
# unbounded history for a "show all" fetch. ──────────────────────────────────────────────────


def _entry(i: int) -> dict:
    return {
        "id": f"t-{i}",
        "date": "2026-01-01T00:00:00+00:00",
        "title": "Update",
        "summary": f"e{i}",
    }


def test_list_feed_bounds_a_long_timeline_and_reports_showing_total(client, db_isolated):
    from backend.db.intel_stories_store import IntelStoriesStore

    store = IntelStoriesStore()
    store.insert(
        {
            "id": "hot-1",
            "type": "asset",
            "status": "monitoring",
            "label": "Hot ticker",
            "current_summary": "",
            "action_items": [],
        }
    )
    for i in range(30):
        store.append_timeline_entry("hot-1", _entry(i))

    rows = client.get("/api/intel/signals").json()
    assert len(rows) == 1
    row = rows[0]
    assert len(row["timeline"]) == 20  # bounded, not all 30
    assert row["showing_timeline"] == 20
    assert row["total_timeline"] == 30

    # The stored entries themselves are untouched — bounding is a READ concern only. A raw
    # story dict from IntelStoriesStore never carries a "timeline" key (RFC
    # intel-signals-data-model) — entries live in signal_timeline_entries, fetched separately.
    assert "timeline" not in store.find_by_id("hot-1")
    assert len(store.entries_for_signal("hot-1", limit=None)) == 30


def test_list_feed_reports_showing_equals_total_for_a_short_timeline(client, db_isolated):
    from backend.db.intel_stories_store import IntelStoriesStore

    store = IntelStoriesStore()
    store.insert(
        {
            "id": "quiet-1",
            "type": "asset",
            "status": "monitoring",
            "label": "Quiet ticker",
            "current_summary": "",
            "action_items": [],
        }
    )
    for i in range(3):
        store.append_timeline_entry("quiet-1", _entry(i))

    row = client.get("/api/intel/signals").json()[0]
    assert len(row["timeline"]) == 3
    assert row["showing_timeline"] == 3
    assert row["total_timeline"] == 3


def test_get_one_signal_returns_full_unbounded_timeline(client, db_isolated):
    from backend.db.intel_stories_store import IntelStoriesStore

    store = IntelStoriesStore()
    store.insert(
        {
            "id": "hot-1",
            "type": "asset",
            "status": "monitoring",
            "label": "Hot ticker",
            "current_summary": "",
            "action_items": [],
        }
    )
    for i in range(30):
        store.append_timeline_entry("hot-1", _entry(i))

    r = client.get("/api/intel/signals/hot-1")
    assert r.status_code == 200
    body = r.json()
    assert len(body["timeline"]) == 30  # the FULL history, unbounded


def test_get_one_signal_unknown_id_404(client, store):
    assert client.get("/api/intel/signals/nope").status_code == 404


def test_get_one_signal_deleted_is_404(client, store):
    store.delete("feed-1")
    assert client.get("/api/intel/signals/feed-1").status_code == 404


def test_get_one_signal_returns_archived_story_too(client, store):
    """Unlike the live-feed listing, the single-signal fetch is not archive-filtered — viewing
    an archived story's full history is a legitimate read reachable from the archive view."""
    r = client.get("/api/intel/signals/arch-1")
    assert r.status_code == 200
    assert r.json()["id"] == "arch-1"


# ── Wire-contract regression: current_summary (DB-internal, RFC §2.1.1) aliases back to the
# frontend's own `summary` field (StoryArcCard.tsx) on every HTTP response. ────────────────────


def test_list_and_get_and_patch_alias_current_summary_to_wire_summary(client, db_isolated):
    from backend.db.intel_stories_store import IntelStoriesStore

    IntelStoriesStore().insert(
        {
            "id": "wire-1",
            "type": "asset",
            "status": "monitoring",
            "label": "Wire ticker",
            "current_summary": "The DB-internal narrative.",
            "action_items": [],
        }
    )

    list_row = client.get("/api/intel/signals").json()[0]
    assert list_row["summary"] == "The DB-internal narrative."
    assert "current_summary" not in list_row

    get_row = client.get("/api/intel/signals/wire-1").json()
    assert get_row["summary"] == "The DB-internal narrative."
    assert "current_summary" not in get_row

    patch_row = client.patch("/api/intel/signals/wire-1", json={"read": True}).json()
    assert patch_row["summary"] == "The DB-internal narrative."
    assert "current_summary" not in patch_row
