"""BudgetEngineStateStore — the default per-(book, engine) config store for a pluggable
budget engine that owns no bespoke store of its own (.claude/rfcs/
2026-08-22-pluggable-budget-engines.md, Decision A/E; table ``budget_engine_state``,
revision 0049). Proves the CRUD round trip, that two engines' configs coexist
independently (the property Decision A depends on — switching the home lens must never
touch another engine's state), the deterministic row-id shape (budget_assignments'
precedent), checkpoint coverage, and the portable-bundle bridge wiring.
"""

from __future__ import annotations

from sqlalchemy import inspect, select

from backend.db.budget_engine_state_store import BudgetEngineStateStore
from backend.db.tables import DEFAULT_BOOK_ID, budget_engine_state


def _raw_rows(engine):
    with engine.connect() as conn:
        return list(conn.execute(select(budget_engine_state)))


# ── Table shape ──────────────────────────────────────────────────────────────────────


def test_budget_engine_state_table_registered_and_shaped():
    from backend.db import tables as t

    assert "budget_engine_state" in t.metadata.tables
    table = t.metadata.tables["budget_engine_state"]
    assert {c.name for c in table.c} == {"id", "book_id", "engine_id", "payload"}
    assert [c.name for c in table.primary_key.columns] == ["id"]
    assert "book_id" in [fk.parent.name for fk in table.foreign_keys]


def test_budget_engine_state_schema_creates_on_both_dialects(db_engine):
    insp = inspect(db_engine)  # SQLite here, Postgres in CI
    assert insp.has_table("budget_engine_state")


# ── CRUD round trip (the brief's own worked example) ────────────────────────────────────


def test_get_missing_engine_returns_none(db_engine):
    s = BudgetEngineStateStore(engine=db_engine)
    assert s.get("proportional") is None


def test_set_then_get_round_trips(db_engine):
    s = BudgetEngineStateStore(engine=db_engine)
    payload = {"targets": {"needs": 50, "wants": 30, "savings": 20}, "mapping": {}}
    stored = s.set("proportional", payload)
    assert stored == payload
    assert s.get("proportional") == payload


def test_set_overwrites_in_place_no_duplicate_row(db_engine):
    s = BudgetEngineStateStore(engine=db_engine)
    s.set("proportional", {"targets": {"needs": 50, "wants": 30, "savings": 20}, "mapping": {}})
    s.set("proportional", {"targets": {"needs": 60, "wants": 20, "savings": 20}, "mapping": {}})
    rows = _raw_rows(db_engine)
    assert len(rows) == 1
    assert s.get("proportional")["targets"]["needs"] == 60


def test_delete_round_trip(db_engine):
    s = BudgetEngineStateStore(engine=db_engine)
    s.set("proportional", {"targets": {}, "mapping": {}})
    assert s.delete("proportional") is True
    assert s.get("proportional") is None
    assert _raw_rows(db_engine) == []


def test_delete_missing_engine_returns_false_and_writes_nothing(db_engine):
    s = BudgetEngineStateStore(engine=db_engine)
    assert s.delete("proportional") is False
    assert _raw_rows(db_engine) == []


def test_persistence_across_store_instances(db_engine):
    s1 = BudgetEngineStateStore(engine=db_engine)
    s1.set("proportional", {"targets": {"needs": 50, "wants": 30, "savings": 20}})
    s2 = BudgetEngineStateStore(engine=db_engine)
    assert s2.get("proportional") == {"targets": {"needs": 50, "wants": 30, "savings": 20}}


# ── Row-id determinism (budget_assignments' precedent) ──────────────────────────────────


def test_writes_default_book_id_and_deterministic_row_id(db_engine):
    s = BudgetEngineStateStore(engine=db_engine)
    s.set("proportional", {"targets": {}})
    rows = _raw_rows(db_engine)
    assert len(rows) == 1
    assert rows[0].book_id == DEFAULT_BOOK_ID
    assert rows[0].engine_id == "proportional"
    assert rows[0].id == f"{DEFAULT_BOOK_ID}:proportional"


# ── Two engines coexist independently — Decision A's own load-bearing property ─────────


def test_two_engine_ids_coexist_independently(db_engine):
    s = BudgetEngineStateStore(engine=db_engine)
    s.set("proportional", {"targets": {"needs": 50, "wants": 30, "savings": 20}})
    s.set("caps-experimental", {"limits": {"cat_1": 500.0}})

    assert s.get("proportional") == {"targets": {"needs": 50, "wants": 30, "savings": 20}}
    assert s.get("caps-experimental") == {"limits": {"cat_1": 500.0}}
    assert len(_raw_rows(db_engine)) == 2


def test_switching_which_engine_is_edited_never_touches_the_others_row(db_engine):
    """Decision A, mechanized: repeatedly editing one engine's config must never mutate a
    DIFFERENT engine's row — the property that makes "peek any lens, switch home for free"
    safe."""
    s = BudgetEngineStateStore(engine=db_engine)
    s.set("proportional", {"targets": {"needs": 50, "wants": 30, "savings": 20}})
    untouched = {"limits": {"cat_1": 500.0}}
    s.set("caps-experimental", untouched)

    s.set("proportional", {"targets": {"needs": 40, "wants": 30, "savings": 30}})
    s.set("proportional", {"targets": {"needs": 33, "wants": 33, "savings": 34}})

    assert s.get("caps-experimental") == untouched


def test_all_for_book_returns_every_configured_engine(db_engine):
    s = BudgetEngineStateStore(engine=db_engine)
    assert s.all_for_book() == {}

    s.set("proportional", {"targets": {"needs": 50, "wants": 30, "savings": 20}})
    s.set("caps-experimental", {"limits": {"cat_1": 500.0}})

    assert s.all_for_book() == {
        "proportional": {"targets": {"needs": 50, "wants": 30, "savings": 20}},
        "caps-experimental": {"limits": {"cat_1": 500.0}},
    }


def test_all_for_book_does_not_include_a_deleted_engine(db_engine):
    s = BudgetEngineStateStore(engine=db_engine)
    s.set("proportional", {"targets": {}})
    s.set("caps-experimental", {"limits": {}})
    s.delete("proportional")
    assert s.all_for_book() == {"caps-experimental": {"limits": {}}}


# ── Checkpoint coverage + /rewind (db_isolated — the global engine ai.checkpoint reads) ──


def test_checkpoint_not_excluded():
    from backend.ai.checkpoint import _EXCLUDED_TABLES

    assert "budget_engine_state" not in _EXCLUDED_TABLES


def test_set_is_checkpoint_covered_and_rewind_reverts(db_isolated):
    from backend.ai import checkpoint as cp

    store = BudgetEngineStateStore()
    store.set("proportional", {"targets": {"needs": 50, "wants": 30, "savings": 20}})

    ctx, token = cp.begin_turn("s1", 0, "raise savings target")
    try:
        store.set("proportional", {"targets": {"needs": 40, "wants": 30, "savings": 30}})
    finally:
        entry = cp.end_turn(ctx, token)

    assert entry is not None
    assert "budget_engine_state" in entry["db_tables"]
    assert store.get("proportional")["targets"]["savings"] == 30

    cp.restore(entry["id"], mode="data")
    assert store.get("proportional")["targets"]["savings"] == 20


def test_first_ever_write_is_checkpoint_covered_and_rewind_reverts_to_absent(db_isolated):
    """The FIRST write for a fresh engine has no existing row (before_payload=None) — a
    genuinely different write shape than editing an already-seeded row; restore must
    delete the row entirely."""
    from backend.ai import checkpoint as cp

    store = BudgetEngineStateStore()
    assert store.get("proportional") is None

    ctx, token = cp.begin_turn("s2", 0, "set up 50/30/20 for the first time")
    try:
        store.set("proportional", {"targets": {"needs": 50, "wants": 30, "savings": 20}})
    finally:
        entry = cp.end_turn(ctx, token)

    assert entry is not None
    assert store.get("proportional") is not None

    cp.restore(entry["id"], mode="data")
    assert store.get("proportional") is None


def test_delete_is_checkpoint_covered_and_rewind_reverts(db_isolated):
    from backend.ai import checkpoint as cp

    store = BudgetEngineStateStore()
    store.set("proportional", {"targets": {"needs": 50, "wants": 30, "savings": 20}})

    ctx, token = cp.begin_turn("s3", 0, "clear 50/30/20 config")
    try:
        store.delete("proportional")
    finally:
        entry = cp.end_turn(ctx, token)

    assert entry is not None
    assert "budget_engine_state" in entry["db_tables"]
    assert store.get("proportional") is None

    cp.restore(entry["id"], mode="data")
    assert store.get("proportional") == {"targets": {"needs": 50, "wants": 30, "savings": 20}}


def test_conn_composition_atomic_with_callers_transaction(db_isolated):
    from backend.db.engine import locked_unit_of_work
    from backend.db.tables import budget_engine_state as budget_engine_state_table

    store = BudgetEngineStateStore()
    with locked_unit_of_work(budget_engine_state_table) as conn:
        store.set("proportional", {"targets": {"needs": 50, "wants": 30, "savings": 20}}, conn=conn)
    assert store.get("proportional") == {"targets": {"needs": 50, "wants": 30, "savings": 20}}


# ── Portable-bundle bridge + /api/reset wiring ──────────────────────────────────────────


def test_budget_engine_state_json_is_in_the_real_portable_manifest():
    from backend.core import portable

    names = {p.name for p in portable.PORTABLE_FILES}
    assert "budget_engine_state.json" in names


def test_budget_engine_state_json_resets_to_a_dict_not_a_list():
    from backend.core import portable

    assert portable.empty_contents_for("budget_engine_state.json") == "{}"


def test_bridge_registry_entry_round_trips_directly(db_isolated):
    from backend.db import portable_bridge
    from backend.db.engine import get_engine

    bridge = portable_bridge.BRIDGED["budget_engine_state.json"]
    fixture = {
        "proportional": {"targets": {"needs": 50, "wants": 30, "savings": 20}, "mapping": {}},
        "caps-experimental": {"limits": {"cat_1": 500.0}},
    }

    with get_engine().begin() as conn:
        count = bridge.import_fn(conn, fixture)
    assert count == 2

    with get_engine().connect() as conn:
        assert bridge.export_fn(conn) == fixture


def test_reset_wipes_budget_engine_state(db_isolated):
    from backend.db.engine import get_engine
    from backend.services import portability as svc

    store = BudgetEngineStateStore()
    store.set("proportional", {"targets": {"needs": 50, "wants": 30, "savings": 20}})
    store.set("caps-experimental", {"limits": {"cat_1": 500.0}})

    svc.reset_bridged_stores()

    with get_engine().connect() as conn:
        from backend.db import portable_bridge

        assert portable_bridge.BRIDGED["budget_engine_state.json"].export_fn(conn) == {}
    assert store.all_for_book() == {}
