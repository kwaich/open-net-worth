"""CorrectionStore — DB-backed (plan 4a task 2), on BookPayloadTableStore.
Checkpoint-EXCLUDED (ai.checkpoint._EXCLUDED_TABLES, mirroring txn_audit): a
runtime/behavioral signal, not user-authored data — a /rewind must not resurrect a
stale correction history. record()/add_dismissal() do the capped read-modify-write
in ONE base transaction via _append_capped (drop-oldest by seq), replacing the
removed _write_lock/_load_items/_save_items plumbing.
"""

from __future__ import annotations

from backend.db.correction_store import MAX_CORRECTIONS, CorrectionStore


def _event(payee="STARBUCKS", to="food"):
    return {
        "payee_key": payee.lower(),
        "payee_sample": payee,
        "to_category": to,
        "kind": "expense",
    }


def test_record_then_events_round_trip(db_engine):
    store = CorrectionStore(engine=db_engine)
    stored = store.record(_event())
    assert stored["id"].startswith("corr_")
    assert stored["created_at"]
    assert store.events() == [stored]


def test_corrections_capped_drops_oldest(db_engine):
    store = CorrectionStore(engine=db_engine)
    for i in range(MAX_CORRECTIONS + 5):
        store.record({"payee_key": f"p{i}", "to_category": "food"})
    assert len(store.all()) == MAX_CORRECTIONS
    kept = {e["payee_key"] for e in store.events()}
    assert "p0" not in kept  # oldest dropped
    assert "p4" not in kept  # the whole 5-row overflow dropped, not just one
    assert f"p{MAX_CORRECTIONS + 4}" in kept  # newest kept


def test_add_dismissal_idempotent_then_distinct_target(db_engine):
    store = CorrectionStore(engine=db_engine)
    first = store.add_dismissal("starbucks", "food")
    again = store.add_dismissal("starbucks", "food")
    assert first["id"] == again["id"]
    assert len(store.dismissals()) == 1
    store.add_dismissal("starbucks", "transport")
    assert len(store.dismissals()) == 2


def test_events_excludes_dismissals(db_engine):
    store = CorrectionStore(engine=db_engine)
    store.record(_event())
    store.add_dismissal("starbucks", "food")
    assert len(store.events()) == 1
    assert len(store.dismissals()) == 1
    assert len(store.all()) == 2


def test_recent_filters_by_date(db_engine):
    store = CorrectionStore(engine=db_engine)
    store.record({**_event(), "created_at": "2026-01-01T00:00:00Z"})
    store.record({**_event(), "created_at": "2026-06-01T00:00:00Z"})
    recent = store.recent("2026-05-01T00:00:00Z")
    assert len(recent) == 1
    assert recent[0]["created_at"] == "2026-06-01T00:00:00Z"


def test_corrections_table_is_checkpoint_excluded():
    from backend.ai.checkpoint import _EXCLUDED_TABLES

    assert "corrections" in _EXCLUDED_TABLES


def test_corrections_write_is_not_captured_by_checkpoint(db_engine):
    """The store still constructs checkpointed=True (the hook fires); _EXCLUDED_TABLES
    makes it a structural no-op — the established "excluded but real DB store"
    pattern (mirrors txn_audit)."""
    from backend.ai import checkpoint as ckpt

    store = CorrectionStore(engine=db_engine)
    ctx, token = ckpt.begin_turn("s1", 0, "t")
    try:
        store.record(_event())
    finally:
        entry = ckpt.end_turn(ctx, token)
    assert entry is None  # nothing captured this turn — corrections is excluded
