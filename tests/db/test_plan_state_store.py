"""PlanStateStore — DB-backed (plan 4a, task 4). A true per-book SINGLETON (PK = book_id,
never more than one row) holding the whole {scenarios, baseline} dict as payload — proves the
singleton round trip (with _stamp_event_ids still running), scenario/baseline CRUD parity
with the JsonStore era, and checkpoint coverage of a scenario edit / baseline edit.
"""

from __future__ import annotations

from sqlalchemy import select

from backend.core.errors import ValidationError
from backend.db.plan_state_store import PlanStateStore
from backend.db.tables import DEFAULT_BOOK_ID, plan_state


def _raw_rows(engine):
    with engine.connect() as conn:
        return list(conn.execute(select(plan_state)))


# ── Singleton round trip (the brief's own worked example) ──────────────────────────────


def test_plan_state_singleton_round_trip(db_engine):
    s = PlanStateStore(engine=db_engine)
    sc = s.add_scenario({"saving": 1000, "events": [{}]})
    assert sc["id"] and s.list_scenarios()[0]["events"][0].get("id")  # _stamp_event_ids still runs
    s.set_baseline({"retireAge": 50})
    assert s.get_baseline() == {"retireAge": 50}


def test_plan_state_row_is_a_true_per_book_singleton(db_engine):
    """Regardless of how many scenario/baseline writes happen, exactly ONE row ever exists,
    keyed book_id=DEFAULT_BOOK_ID — proven against the raw table, not just the store's own
    reads (which would mask a duplicate-row regression)."""
    s = PlanStateStore(engine=db_engine)
    s.add_scenario({"name": "A"})
    s.add_scenario({"name": "B"})
    s.set_baseline({"saving": 1000})
    s.set_baseline({"saving": 2000})
    rows = _raw_rows(db_engine)
    assert len(rows) == 1
    assert rows[0].book_id == DEFAULT_BOOK_ID
    assert len(rows[0].payload["scenarios"]) == 2
    assert rows[0].payload["baseline"] == {"saving": 2000}


def test_get_state_empty_by_default(db_engine):
    s = PlanStateStore(engine=db_engine)
    assert s.get_state() == {"scenarios": [], "baseline": None}
    assert _raw_rows(db_engine) == []  # no row minted until the first write


# ── Scenario CRUD parity ────────────────────────────────────────────────────────────────


def test_add_scenario_assigns_server_id(db_engine):
    s = PlanStateStore(engine=db_engine)
    scen = s.add_scenario({"name": "Retire early", "ret": 7.0, "saving": 3000})
    assert "id" in scen
    assert scen["id"].startswith("scen_")


def test_list_scenarios_reflects_added(db_engine):
    s = PlanStateStore(engine=db_engine)
    s.add_scenario({"name": "A", "ret": 6.0})
    s.add_scenario({"name": "B", "ret": 8.0})
    scenarios = s.list_scenarios()
    assert len(scenarios) == 2
    assert {sc["name"] for sc in scenarios} == {"A", "B"}


def test_update_scenario_replaces_in_place_and_id_stable(db_engine):
    s = PlanStateStore(engine=db_engine)
    scen = s.add_scenario({"name": "Old name", "ret": 7.0})
    original_id = scen["id"]
    updated = s.update_scenario(original_id, {"name": "New name", "ret": 8.0, "id": "ignored"})
    assert updated["name"] == "New name"
    assert updated["id"] == original_id
    assert s.list_scenarios()[0]["name"] == "New name"


def test_update_scenario_returns_none_for_missing(db_engine):
    s = PlanStateStore(engine=db_engine)
    assert s.update_scenario("scen_nonexistent", {"name": "x"}) is None


def test_delete_scenario_round_trip(db_engine):
    s = PlanStateStore(engine=db_engine)
    scen = s.add_scenario({"name": "To delete", "ret": 5.0})
    assert s.delete_scenario(scen["id"]) is True
    assert s.list_scenarios() == []


def test_delete_scenario_returns_false_for_missing(db_engine):
    s = PlanStateStore(engine=db_engine)
    assert s.delete_scenario("scen_no_such") is False


def test_scenario_cap_raises_validation_error(db_engine, monkeypatch):
    import pytest

    import backend.db.plan_state_store as store_mod

    monkeypatch.setattr(store_mod, "MAX_SCENARIOS", 2)
    s = PlanStateStore(engine=db_engine)
    s.add_scenario({"name": "A"})
    s.add_scenario({"name": "B"})
    with pytest.raises(ValidationError, match="limit"):
        s.add_scenario({"name": "C"})


def test_scenario_cap_does_not_persist_the_rejected_scenario(db_engine, monkeypatch):
    """The cap check runs BEFORE the row is mutated/persisted — a rejected add must leave
    the stored scenario count unchanged (proven against the raw row, not just list_scenarios)."""
    import pytest

    import backend.db.plan_state_store as store_mod

    monkeypatch.setattr(store_mod, "MAX_SCENARIOS", 2)
    s = PlanStateStore(engine=db_engine)
    s.add_scenario({"name": "A"})
    s.add_scenario({"name": "B"})
    with pytest.raises(ValidationError, match="limit"):
        s.add_scenario({"name": "C"})
    rows = _raw_rows(db_engine)
    assert len(rows) == 1
    assert len(rows[0].payload["scenarios"]) == 2


# ── Baseline parity ──────────────────────────────────────────────────────────────────────


def test_baseline_set_get_and_clear(db_engine):
    s = PlanStateStore(engine=db_engine)
    baseline = {"saving": 4000, "ret": 7.5, "lifeExp": 90}
    stored = s.set_baseline(baseline)
    assert stored == baseline
    assert s.get_baseline() == baseline
    s.set_baseline(None)
    assert s.get_baseline() is None


def test_baseline_and_scenarios_coexist(db_engine):
    s = PlanStateStore(engine=db_engine)
    s.add_scenario({"name": "S1", "ret": 6.0})
    s.set_baseline({"saving": 5000})
    state = s.get_state()
    assert len(state["scenarios"]) == 1
    assert state["baseline"]["saving"] == 5000


# ── Life-event identity (2026-07-18 incident): _stamp_event_ids still runs verbatim ──────


def test_baseline_events_get_distinct_server_minted_ids(db_engine):
    s = PlanStateStore(engine=db_engine)
    stored = s.set_baseline(
        {
            "events": [
                {"label": "Home", "amount": -500000, "age": 30},
                {"label": "Car", "amount": -200000, "age": 32},
            ]
        }
    )
    ids = [ev["id"] for ev in stored["events"]]
    assert all(ids), "every persisted life event must carry an id"
    assert len(set(ids)) == 2, "life events must not share an id"
    assert s.get_baseline()["events"][1]["id"] == ids[1]


def test_caller_supplied_event_id_is_preserved(db_engine):
    s = PlanStateStore(engine=db_engine)
    stored = s.set_baseline({"events": [{"id": "e-keep", "label": "X", "amount": -1, "age": 40}]})
    assert stored["events"][0]["id"] == "e-keep"


def test_scenario_events_get_ids_on_add_and_update(db_engine):
    s = PlanStateStore(engine=db_engine)
    scen = s.add_scenario({"name": "s", "events": [{"label": "X", "amount": -1, "age": 40}]})
    assert scen["events"][0]["id"]
    updated = s.update_scenario(
        scen["id"], {"name": "s", "events": [{"label": "Y", "amount": -2, "age": 41}]}
    )
    assert updated["events"][0]["id"]


# ── Checkpoint coverage + /rewind (db_isolated — the global engine ai.checkpoint reads) ──


def test_scenario_edit_is_checkpoint_covered_and_rewind_reverts(db_isolated):
    from backend.ai import checkpoint as cp

    store = PlanStateStore()
    scen = store.add_scenario({"name": "Before", "ret": 6.0})  # pre-turn, outside any checkpoint

    ctx, token = cp.begin_turn("s1", 0, "rename scenario")
    try:
        store.update_scenario(scen["id"], {"name": "After", "ret": 9.0})
    finally:
        entry = cp.end_turn(ctx, token)

    assert entry is not None
    assert "plan_state" in entry["db_tables"]
    assert store.list_scenarios()[0]["name"] == "After"

    cp.restore(entry["id"], mode="data")
    reverted = store.list_scenarios()
    assert len(reverted) == 1
    assert reverted[0]["name"] == "Before"
    assert reverted[0]["id"] == scen["id"]


def test_baseline_edit_is_checkpoint_covered_and_rewind_reverts(db_isolated):
    from backend.ai import checkpoint as cp

    store = PlanStateStore()
    store.set_baseline({"saving": 1000})  # pre-turn baseline, outside any checkpoint

    ctx, token = cp.begin_turn("s2", 0, "raise saving rate")
    try:
        store.set_baseline({"saving": 5000})
    finally:
        entry = cp.end_turn(ctx, token)

    assert entry is not None
    assert "plan_state" in entry["db_tables"]
    assert store.get_baseline() == {"saving": 5000}

    cp.restore(entry["id"], mode="data")
    assert store.get_baseline() == {"saving": 1000}


def test_first_ever_write_is_checkpoint_covered_and_rewind_reverts_to_empty(db_isolated):
    """The FIRST write to a fresh book has no existing row (before_payload=None) — a
    genuinely different write shape than editing an already-seeded row; restore must
    delete the row entirely, reverting to the empty-state default."""
    from backend.ai import checkpoint as cp

    store = PlanStateStore()
    assert store.get_state() == {"scenarios": [], "baseline": None}

    ctx, token = cp.begin_turn("s3", 0, "first scenario ever")
    try:
        store.add_scenario({"name": "First"})
    finally:
        entry = cp.end_turn(ctx, token)

    assert entry is not None
    assert len(store.list_scenarios()) == 1

    cp.restore(entry["id"], mode="data")
    assert store.get_state() == {"scenarios": [], "baseline": None}


def test_conn_composition_atomic_with_callers_transaction(db_isolated):
    from backend.db.engine import locked_unit_of_work
    from backend.db.tables import plan_state as plan_state_table

    store = PlanStateStore()
    with locked_unit_of_work(plan_state_table) as conn:
        store.set_baseline({"saving": 777}, conn=conn)
    assert store.get_baseline() == {"saving": 777}


# ── Home budget-engine lens (pluggable-budget-engines, RFC
#    2026-08-22-pluggable-budget-engines.md, Decision E, Phase 2) ──────────────────────


def test_set_home_engine_id_persists_and_get_state_carries_it(db_engine):
    s = PlanStateStore(engine=db_engine)
    assert s.set_home_engine_id("envelope") == {"home_engine_id": "envelope"}
    assert s.get_state()["budget"] == {"home_engine_id": "envelope"}


def test_set_home_engine_id_does_not_disturb_scenarios_or_baseline(db_engine):
    """The home-lens write shares the singleton row with scenarios/baseline — it must
    read-modify-write, never clobber the other two fields."""
    s = PlanStateStore(engine=db_engine)
    s.add_scenario({"name": "A"})
    s.set_baseline({"saving": 1000})
    s.set_home_engine_id("proportional")
    state = s.get_state()
    assert len(state["scenarios"]) == 1
    assert state["baseline"] == {"saving": 1000}
    assert state["budget"] == {"home_engine_id": "proportional"}


def test_set_home_engine_id_is_still_a_true_singleton_row(db_engine):
    """Regardless of how many home-lens writes happen, exactly ONE row ever exists —
    proven against the raw table, mirroring test_plan_state_row_is_a_true_per_book_singleton."""
    s = PlanStateStore(engine=db_engine)
    s.set_home_engine_id("envelope")
    s.set_home_engine_id("proportional")
    s.set_home_engine_id("zero_based")
    rows = _raw_rows(db_engine)
    assert len(rows) == 1
    assert rows[0].book_id == DEFAULT_BOOK_ID
    assert rows[0].payload["budget"] == {"home_engine_id": "zero_based"}


def test_get_state_with_no_home_lens_ever_set_omits_the_budget_key(db_engine):
    """A book that never switched its home lens keeps the EXACT pre-Phase-2
    {scenarios, baseline} shape — no budget key defaulted in, so
    test_get_state_empty_by_default's byte-for-byte equality never has to change."""
    s = PlanStateStore(engine=db_engine)
    s.add_scenario({"name": "A"})
    assert "budget" not in s.get_state()


def test_home_engine_edit_is_checkpoint_covered_and_rewind_reverts(db_isolated):
    from backend.ai import checkpoint as cp

    store = PlanStateStore()
    store.set_home_engine_id("zero_based")  # pre-turn, outside any checkpoint

    ctx, token = cp.begin_turn("s4", 0, "switch home budget lens")
    try:
        store.set_home_engine_id("envelope")
    finally:
        entry = cp.end_turn(ctx, token)

    assert entry is not None
    assert "plan_state" in entry["db_tables"]
    assert store.get_state()["budget"] == {"home_engine_id": "envelope"}

    cp.restore(entry["id"], mode="data")
    assert store.get_state()["budget"] == {"home_engine_id": "zero_based"}
