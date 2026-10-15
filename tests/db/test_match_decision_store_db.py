"""MatchDecisionStore — DB-backed (plan 4a task 2), on BookPayloadTableStore. PK is
``pair_key`` (id_field override) — the generic ``id`` column holds the pair_key value.
``forget`` drops the removed _write_lock/_load_items fallback for a signature-keyed
delete (display/admin path) in favor of a plain self.all() scan.
"""

from __future__ import annotations

from backend.db import planning_io as pio
from backend.db.match_decision_store import MatchDecisionStore, make_pair_key


def test_remember_then_get_for_pair_round_trip(db_engine):
    store = MatchDecisionStore(engine=db_engine)
    store.remember(
        {"incoming_id": "a", "canonical_id": "b", "signature": "sig1", "verdict": "same"}
    )
    decision = store.get_for_pair("a", "b")
    assert decision is not None
    assert decision["pair_key"] == make_pair_key("a", "b")
    assert decision["verdict"] == "same"
    assert decision["created_at"]  # stamped on new entries


def test_remember_replaces_existing_pair_in_place(db_engine):
    store = MatchDecisionStore(engine=db_engine)
    store.remember(
        {"incoming_id": "a", "canonical_id": "b", "signature": "sig1", "verdict": "same"}
    )
    store.remember(
        {"incoming_id": "a", "canonical_id": "b", "signature": "sig1", "verdict": "different"}
    )
    assert store.total_count() == 1
    assert store.get_for_pair("a", "b")["verdict"] == "different"


def test_match_decision_forget_by_signature_fallback(db_engine):
    store = MatchDecisionStore(engine=db_engine)
    store.remember(
        {"incoming_id": "a", "canonical_id": "b", "signature": "sig1", "verdict": "same"}
    )
    assert store.forget("sig1") is True  # deletes via signature fallback
    assert store.get("sig1") is None


def test_forget_by_pair_key_direct(db_engine):
    store = MatchDecisionStore(engine=db_engine)
    store.remember(
        {"incoming_id": "a", "canonical_id": "b", "signature": "sig1", "verdict": "same"}
    )
    pair_key = make_pair_key("a", "b")
    assert store.forget(pair_key) is True
    assert store.get_for_pair("a", "b") is None


def test_forget_missing_returns_false(db_engine):
    store = MatchDecisionStore(engine=db_engine)
    assert store.forget("nope") is False


def test_forget_deletes_legacy_row_absorbed_without_pair_key_by_surrogate_id(db_engine):
    """I1 regression (plan 4a whole-branch final review): a legacy match_decision row
    (no ``pair_key`` in its payload — the pre-F02 shape) absorbed by revision 0010 gets
    a deterministic ``mdlegacy_<sha16>`` surrogate in the ``id`` PK column ONLY
    (``planning_io._match_decision_id``) — the surrogate is never written into payload,
    by design, so ``row.get("pair_key")`` on such a row is always None. ``forget``'s
    signature-fallback scan used to call ``self.delete(row.get("pair_key"))`` — i.e.
    ``self.delete(None)`` for exactly this row shape — which matches nothing, so the
    row survived and ``forget`` wrongly returned False."""
    with db_engine.begin() as conn:
        pio.import_match_decisions(conn, [{"signature": "sig_legacy_1", "verdict": "same"}])

    store = MatchDecisionStore(engine=db_engine)
    assert store.get("sig_legacy_1") is not None
    assert store.forget("sig_legacy_1") is True
    assert store.get("sig_legacy_1") is None
    assert store.total_count() == 0


def test_recent_orders_newest_first_with_offset(db_engine):
    store = MatchDecisionStore(engine=db_engine)
    store.remember(
        {
            "incoming_id": "a",
            "canonical_id": "b",
            "created_at": "2026-06-01T00:00:00Z",
        }
    )
    store.remember(
        {
            "incoming_id": "c",
            "canonical_id": "d",
            "created_at": "2026-07-01T00:00:00Z",
        }
    )
    recent = store.recent(limit=1)
    assert recent[0]["incoming_id"] == "c"
    older = store.recent(limit=1, offset=1)
    assert older[0]["incoming_id"] == "a"
