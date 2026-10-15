"""CRUD/query regression test for MatchReviewStore — the dedup review queue.

Bug: ``pending()`` returned raw insertion (oldest-first) order with no sort at all, so a
stale, never-triaged review from weeks ago always rendered ahead of a fresh, actionable
one in the Inbox's "Duplicate review" section. Fixed by sorting ``pending()`` newest-first
by ``created_at`` — the lowest layer, so ``services.match_review.list_reviews()`` (which
only filters, via ``_sweep_zombies``, never reorders) and every reader above it inherit
the fix for free.
"""

from __future__ import annotations

from backend.db.match_review_store import MatchReviewStore


def _row(id_, created_at, *, status="pending", batch_id=None):
    return {
        "id": id_,
        "incoming_id": f"txn_{id_}_in",
        "canonical_id": f"txn_{id_}_canon",
        "klass": "near_duplicate",
        "score": 0.9,
        "become_transfer": False,
        "signature": "sig",
        "reasons": [],
        "llm_reason": None,
        "batch_id": batch_id,
        "alternatives": [],
        "status": status,
        "created_at": created_at,
    }


def _store(tmp_path) -> MatchReviewStore:
    store = MatchReviewStore()
    store._path = tmp_path / "match_reviews.json"
    return store


def test_pending_returns_newest_first_regardless_of_insertion_order(tmp_path):
    store = _store(tmp_path)

    # Inserted oldest-first (raw insertion order) — the exact shape that used to leak
    # straight through as "sort order" before this fix.
    store.insert(_row("old", "2026-06-15T00:00:00Z"))
    store.insert(_row("mid", "2026-07-01T00:00:00Z"))
    store.insert(_row("new", "2026-07-21T00:00:00Z"))

    ids = [r["id"] for r in store.pending()]
    assert ids == ["new", "mid", "old"], (
        f"pending() must sort newest-first by created_at, got {ids}"
    )


def test_pending_batch_filter_still_sorts_newest_first(tmp_path):
    store = _store(tmp_path)

    store.insert(_row("b1_old", "2026-06-01T00:00:00Z", batch_id="batch1"))
    store.insert(_row("b1_new", "2026-07-20T00:00:00Z", batch_id="batch1"))
    store.insert(_row("b2_only", "2026-07-10T00:00:00Z", batch_id="batch2"))

    ids = [r["id"] for r in store.pending("batch1")]
    assert ids == ["b1_new", "b1_old"]


def test_pending_excludes_non_pending_status(tmp_path):
    store = _store(tmp_path)

    store.insert(_row("merged", "2026-07-21T00:00:00Z", status="merged"))
    store.insert(_row("pending_row", "2026-07-01T00:00:00Z"))

    ids = [r["id"] for r in store.pending()]
    assert ids == ["pending_row"]
