"""MatchReviewStore — DB-backed (plan 4a task 2), on BookPayloadTableStore."""

from __future__ import annotations

from backend.db.match_review_store import MatchReviewStore


def _enqueue(store, incoming_id, created_at=None, **kw):
    row = store.enqueue(
        {
            "incoming_id": incoming_id,
            "canonical_id": "txn_c",
            "klass": "near_duplicate",
            "score": 0.9,
            **kw,
        }
    )
    if created_at is not None:
        store.update_by_id(row["id"], {"created_at": created_at})
        row = store.find_by_id(row["id"])
    return row


def test_enqueue_then_find_round_trip(db_engine):
    store = MatchReviewStore(engine=db_engine)
    row = _enqueue(store, "txn_a")
    assert row["id"].startswith("mr_")
    assert row["status"] == "pending"
    assert store.find_by_id(row["id"])["incoming_id"] == "txn_a"


def test_pending_sorts_newest_first(db_engine):
    store = MatchReviewStore(engine=db_engine)
    _enqueue(store, "old", created_at="2026-06-01T00:00:00Z")
    _enqueue(store, "new", created_at="2026-07-01T00:00:00Z")
    ids = [r["incoming_id"] for r in store.pending()]
    assert ids == ["new", "old"]


def test_pending_filters_by_batch_id(db_engine):
    store = MatchReviewStore(engine=db_engine)
    _enqueue(store, "b1", batch_id="batch1")
    _enqueue(store, "b2", batch_id="batch2")
    ids = [r["incoming_id"] for r in store.pending("batch1")]
    assert ids == ["b1"]


def test_set_status_and_by_incoming(db_engine):
    store = MatchReviewStore(engine=db_engine)
    row = _enqueue(store, "txn_a")
    updated = store.set_status(row["id"], "merged")
    assert updated["status"] == "merged"
    assert store.by_incoming("txn_a")["status"] == "merged"
    assert [r["id"] for r in store.pending()] == []  # no longer pending
