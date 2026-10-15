"""NamespacedSettingsStore — a dict-shaped singleton config keyed by a fixed namespace
string, ONE row per namespace in the shared ``settings`` table (plan 4d, task 1). Proves the
get/save round trip, namespace isolation (two namespaces never collide in one shared table),
whole-payload replace semantics (a save is never a per-key patch), the single-row-per-namespace
upsert invariant, checkpoint coverage of a settings edit, and conn= composition — mirrors
test_plan_state_store.py's depth for PlanStateStore, scaled to this store's much smaller
(get/save only) surface.
"""

from __future__ import annotations

from sqlalchemy import select

from backend.db.payload_store import NamespacedSettingsStore
from backend.db.tables import settings


def _raw_rows(engine):
    with engine.connect() as conn:
        return list(conn.execute(select(settings)))


# ── get/save round trip (the brief's own worked example) ───────────────────────────────


def test_namespaced_settings_round_trip(db_engine):
    s = NamespacedSettingsStore("memory_settings", engine=db_engine)
    assert s.get() == {}
    s.save({"enabled": False})
    assert s.get() == {"enabled": False}


def test_get_returns_empty_dict_when_no_row_minted_yet(db_engine):
    s = NamespacedSettingsStore("preferred_models", engine=db_engine)
    assert s.get() == {}
    assert _raw_rows(db_engine) == []  # no row minted until the first save


# ── Namespace isolation + singleton-per-namespace upsert ───────────────────────────────


def test_two_namespaces_are_independent_rows(db_engine):
    memory = NamespacedSettingsStore("memory_settings", engine=db_engine)
    models = NamespacedSettingsStore("preferred_models", engine=db_engine)
    memory.save({"enabled": False})
    models.save({"models": ["anthropic:claude-sonnet-5"]})
    assert memory.get() == {"enabled": False}
    assert models.get() == {"models": ["anthropic:claude-sonnet-5"]}
    rows = _raw_rows(db_engine)
    assert {r.namespace for r in rows} == {"memory_settings", "preferred_models"}


def test_save_replaces_the_whole_payload_not_a_patch(db_engine):
    s = NamespacedSettingsStore("memory_settings", engine=db_engine)
    s.save({"enabled": False, "extra": "keep?"})
    s.save({"enabled": True})
    assert s.get() == {"enabled": True}  # "extra" is gone — a save is a wholesale replace


def test_save_upserts_a_single_row_per_namespace(db_engine):
    """Two saves to the same namespace must update IN PLACE, never mint a second row —
    proven against the raw table, not just the store's own read (which would mask a
    duplicate-row regression, mirroring test_plan_state_row_is_a_true_per_book_singleton)."""
    s = NamespacedSettingsStore("memory_settings", engine=db_engine)
    s.save({"enabled": False})
    s.save({"enabled": True})
    rows = _raw_rows(db_engine)
    assert len(rows) == 1
    assert rows[0].namespace == "memory_settings"
    assert rows[0].payload == {"enabled": True}


# ── Checkpoint coverage + /rewind (db_isolated — the global engine ai.checkpoint reads) ──


def test_settings_edit_is_checkpoint_covered_and_rewind_reverts(db_isolated):
    from backend.ai import checkpoint as cp

    store = NamespacedSettingsStore("memory_settings")
    store.save({"enabled": True})  # pre-turn, outside any checkpoint

    ctx, token = cp.begin_turn("s1", 0, "disable memory")
    try:
        store.save({"enabled": False})
    finally:
        entry = cp.end_turn(ctx, token)

    assert entry is not None
    assert "settings" in entry["db_tables"]
    assert store.get() == {"enabled": False}

    cp.restore(entry["id"], mode="data")
    assert store.get() == {"enabled": True}


def test_first_ever_write_is_checkpoint_covered_and_rewind_reverts_to_empty(db_isolated):
    """The FIRST write to a fresh namespace has no existing row (before_payload=None) — a
    genuinely different write shape than editing an already-seeded row; restore must delete
    the row entirely, reverting to the empty-dict default (mirrors PlanStateStore's own
    first-write checkpoint test)."""
    from backend.ai import checkpoint as cp

    store = NamespacedSettingsStore("preferred_models")
    assert store.get() == {}

    ctx, token = cp.begin_turn("s2", 0, "set preferred models")
    try:
        store.save({"models": ["x"]})
    finally:
        entry = cp.end_turn(ctx, token)

    assert entry is not None
    assert store.get() == {"models": ["x"]}

    cp.restore(entry["id"], mode="data")
    assert store.get() == {}


# ── conn= composition (the choke-point seam every multi-store money write composes through) ──


def test_conn_composition_atomic_with_callers_transaction(db_isolated):
    from backend.db.engine import locked_unit_of_work
    from backend.db.tables import settings as settings_table

    store = NamespacedSettingsStore("preferred_models")
    with locked_unit_of_work(settings_table) as conn:
        store.save({"models": ["x"]}, conn=conn)
    assert store.get() == {"models": ["x"]}
