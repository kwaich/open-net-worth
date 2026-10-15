"""AssetBriefStore on PayloadTableStore (plan 4b, task 3) — a Tier-2 cache (NO book_id,
mirrors instruments' shape: PK is instrument_id, keyed onto the generic id column) that is
checkpoint-EXCLUDED, like evidence/price_points/instruments' non-existent-here-sibling: a
derived LLM/search cache a /rewind must never resurrect or revert.
"""

from __future__ import annotations


def _brief(instrument_id: str, description: str = "test") -> dict:
    return {
        "instrument_id": instrument_id,
        "news": [],
        "key_issues": [],
        "facts": {},
        "peers": [],
        "description": description,
        "synthesized_at": "2026-01-01T00:00:00+00:00",
        "search_provider": "tavily",
    }


# ── CRUD parity with the JsonStore era ───────────────────────────────────────────


def test_get_absent_row_returns_none(db_engine):
    from backend.db.asset_brief_store import AssetBriefStore

    store = AssetBriefStore(engine=db_engine)
    assert store.get("nonexistent") is None


def test_upsert_and_get(db_engine):
    from backend.db.asset_brief_store import AssetBriefStore

    store = AssetBriefStore(engine=db_engine)
    store.upsert_brief(_brief("inst-1", "A description"))
    got = store.get("inst-1")
    assert got is not None
    assert got["description"] == "A description"
    assert got["instrument_id"] == "inst-1"


def test_last_write_wins(db_engine):
    from backend.db.asset_brief_store import AssetBriefStore

    store = AssetBriefStore(engine=db_engine)
    store.upsert_brief(_brief("inst-1", "first"))
    store.upsert_brief(_brief("inst-1", "second"))
    got = store.get("inst-1")
    assert got["description"] == "second"


def test_multiple_instruments(db_engine):
    from backend.db.asset_brief_store import AssetBriefStore

    store = AssetBriefStore(engine=db_engine)
    store.upsert_brief(_brief("inst-1", "A"))
    store.upsert_brief(_brief("inst-2", "B"))
    assert store.get("inst-1")["description"] == "A"
    assert store.get("inst-2")["description"] == "B"


def test_get_unknown_returns_none(db_engine):
    from backend.db.asset_brief_store import AssetBriefStore

    store = AssetBriefStore(engine=db_engine)
    store.upsert_brief(_brief("inst-1"))
    assert store.get("inst-9999") is None


def test_upsert_replaces_full_record(db_engine):
    from backend.db.asset_brief_store import AssetBriefStore

    store = AssetBriefStore(engine=db_engine)
    store.upsert_brief(_brief("inst-1", "old"))
    updated = {**_brief("inst-1", "new"), "search_provider": "tavily"}
    store.upsert_brief(updated)
    got = store.get("inst-1")
    assert got["description"] == "new"
    assert got["search_provider"] == "tavily"


def test_upsert_keeps_same_row_seq_on_replace(db_engine):
    """A replace keeps the row's seq (PayloadTableStore.upsert's documented contract) —
    proven against the raw table, not just the store's own read."""
    from sqlalchemy import select

    from backend.db.asset_brief_store import AssetBriefStore
    from backend.db.tables import asset_brief

    store = AssetBriefStore(engine=db_engine)
    store.upsert_brief(_brief("inst-1", "old"))
    store.upsert_brief(_brief("inst-2", "other"))
    store.upsert_brief(_brief("inst-1", "new"))
    with db_engine.connect() as conn:
        rows = {r.id: r.seq for r in conn.execute(select(asset_brief))}
    assert rows["inst-1"] == 0  # unchanged seq — in-place update, not a re-append
    assert rows["inst-2"] == 1


# ── Checkpoint exclusion — a /rewind must not resurrect this derived cache ───────


def test_asset_brief_upsert_and_excluded(db_engine):
    """The brief's own worked example: CRUD works AND the table name is registered
    checkpoint-excluded."""
    from backend.ai.checkpoint import _EXCLUDED_TABLES
    from backend.db.asset_brief_store import AssetBriefStore

    s = AssetBriefStore(engine=db_engine)
    s.upsert_brief({"instrument_id": "AAPL", "description": "x"})
    assert s.get("AAPL")["description"] == "x"
    assert "asset_brief" in _EXCLUDED_TABLES


def test_checkpoint_does_not_capture_or_revert_asset_brief_writes(db_isolated):
    """Behavioral proof, not just the frozenset membership: a brief written mid-turn is
    NOT captured by the checkpoint and NOT reverted by restore — mirrors evidence's own
    exclusion contract (tests/db/test_write_log_checkpoints.py::test_excluded_table_not_captured)."""
    from backend.ai import checkpoint as cp
    from backend.db.asset_brief_store import AssetBriefStore

    store = AssetBriefStore()
    store.upsert_brief(_brief("inst-1", "before-turn"))

    ctx, token = cp.begin_turn("s1", 0, "refresh brief")
    store.upsert_brief(_brief("inst-1", "during-turn"))
    entry = cp.end_turn(ctx, token)

    # Nothing ELSE was written this turn, so either no checkpoint was minted at all, or
    # one was minted but asset_brief is not among its db_tables.
    assert entry is None or "asset_brief" not in entry.get("db_tables", [])

    if entry is not None:
        cp.restore(entry["id"], mode="data")
    assert store.get("inst-1")["description"] == "during-turn"  # unreverted
