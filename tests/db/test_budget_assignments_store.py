"""BudgetAssignmentsStore — DB-backed (plan 4a, task 4). The one planning store whose money
is a promoted decimal-Text column (repr(float)-exact, never a native Float) — proves the
decimal-exactness round trip and the sparse-delete-on-exact-zero invariant directly against
the real table, plus book_id/row-id determinism, upsert-in-place, checkpoint coverage, and
conn= choke composition.
"""

from __future__ import annotations

import threading

import pytest
from sqlalchemy import select

from backend.core.errors import ValidationError
from backend.db.budget_assignments_store import BudgetAssignmentsStore
from backend.db.tables import DEFAULT_BOOK_ID, budget_assignments


def _raw_rows(engine):
    with engine.connect() as conn:
        return list(conn.execute(select(budget_assignments)))


# ── Decimal exactness + sparse-delete-on-zero (the brief's own worked example) ─────────


def test_set_assigned_stores_exact_decimal_and_deletes_on_zero(db_engine):
    s = BudgetAssignmentsStore(engine=db_engine)
    s.set_assigned("2026-06", "food", 0.1 + 0.2)
    assert s.assigned("2026-06", "food") == round(0.1 + 0.2, 2)
    s.set_assigned("2026-06", "food", 0.0)  # exact-zero removes the row
    assert s.month_map("2026-06") == {}


def test_amount_column_is_decimal_text_and_round_trips_exactly(db_engine):
    """The promoted `amount` column is Text holding repr(float)-exact digits — never a
    native Float storage class. Proven directly against the raw row, not just via the
    store's own read path (which would mask a storage-class regression)."""
    s = BudgetAssignmentsStore(engine=db_engine)
    s.set_assigned("2026-06", "food", 0.1 + 0.2)
    rows = _raw_rows(db_engine)
    assert len(rows) == 1
    raw_amount = rows[0].amount
    assert isinstance(raw_amount, str), f"amount must be stored as TEXT, got {type(raw_amount)}"
    assert raw_amount == repr(round(0.1 + 0.2, 2))
    assert float(raw_amount) == round(0.1 + 0.2, 2)


def test_set_assigned_zero_on_absent_row_is_a_noop(db_engine):
    """Setting 0 on a (month, category) that was never assigned must not insert a row at
    all — the sparse-file invariant applies to the never-written case too, not just the
    zero-after-nonzero case."""
    s = BudgetAssignmentsStore(engine=db_engine)
    assert s.set_assigned("2026-06", "food", 0.0) == 0.0
    assert s.month_map("2026-06") == {}
    assert _raw_rows(db_engine) == []


def test_set_assigned_delete_removes_exactly_one_row(db_engine):
    """Sparse-delete-on-zero removes the row for THIS (month, category) only — a sibling
    category in the same month survives untouched."""
    s = BudgetAssignmentsStore(engine=db_engine)
    s.set_assigned("2026-06", "food", 800.0)
    s.set_assigned("2026-06", "transport", 200.0)
    assert len(_raw_rows(db_engine)) == 2
    s.set_assigned("2026-06", "food", 0.0)
    rows = _raw_rows(db_engine)
    assert len(rows) == 1
    assert rows[0].category == "transport"


# ── M1 (security review, plan 4a task 9): reject non-finite/non-numeric amounts — the
# SAME guard the money core uses (money_io._assert_finite_money, FR-M7). Before this fix,
# set_assigned(month, cat, float("inf")) stored TEXT 'inf' and read back inf, poisoning
# budget/RTA display math (inf - inf -> nan). ───────────────────────────────────────────


@pytest.mark.parametrize("bad", [float("inf"), float("-inf"), float("nan")])
def test_set_assigned_rejects_non_finite_amount(db_engine, bad):
    s = BudgetAssignmentsStore(engine=db_engine)
    with pytest.raises(ValidationError):
        s.set_assigned("2026-06", "food", bad)


def test_set_assigned_rejects_non_numeric_amount(db_engine):
    s = BudgetAssignmentsStore(engine=db_engine)
    with pytest.raises(ValidationError):
        s.set_assigned("2026-06", "food", "abc")


def test_set_assigned_rejects_non_finite_amount_writes_nothing(db_engine):
    s = BudgetAssignmentsStore(engine=db_engine)
    with pytest.raises(ValidationError):
        s.set_assigned("2026-06", "food", float("inf"))
    assert _raw_rows(db_engine) == []


def test_set_assigned_rejects_non_finite_amount_leaves_existing_row_untouched(db_engine):
    """The guard fires BEFORE the read-modify-write, so a rejected update must leave a
    pre-existing assignment exactly as it was — no partial mutation."""
    s = BudgetAssignmentsStore(engine=db_engine)
    s.set_assigned("2026-06", "food", 500.0)
    with pytest.raises(ValidationError):
        s.set_assigned("2026-06", "food", float("nan"))
    assert s.assigned("2026-06", "food") == 500.0


def test_set_assigned_normal_amount_still_works_after_the_guard(db_engine):
    s = BudgetAssignmentsStore(engine=db_engine)
    assert s.set_assigned("2026-06", "food", 123.45) == 123.45
    assert s.assigned("2026-06", "food") == 123.45


# ── book_id / row-id determinism ────────────────────────────────────────────────────────


def test_writes_default_book_id_and_deterministic_row_id(db_engine):
    s = BudgetAssignmentsStore(engine=db_engine)
    s.set_assigned("2026-06", "food", 500.0)
    rows = _raw_rows(db_engine)
    assert len(rows) == 1
    assert rows[0].book_id == DEFAULT_BOOK_ID
    assert rows[0].id == f"{DEFAULT_BOOK_ID}:2026-06:food"


def test_set_assigned_updates_existing_row_in_place_no_duplicate(db_engine):
    s = BudgetAssignmentsStore(engine=db_engine)
    s.set_assigned("2026-06", "food", 100.0)
    s.set_assigned("2026-06", "food", 250.0)
    rows = _raw_rows(db_engine)
    assert len(rows) == 1
    assert float(rows[0].amount) == 250.0
    assert s.assigned("2026-06", "food") == 250.0


# ── Reads: parity with the JsonStore-era behavior ───────────────────────────────────────


def test_month_map_and_assigned_default_empty(db_engine):
    s = BudgetAssignmentsStore(engine=db_engine)
    assert s.month_map("2026-06") == {}
    assert s.assigned("2026-06", "food") == 0.0
    assert s.months() == []


def test_set_and_read_multiple_categories(db_engine):
    s = BudgetAssignmentsStore(engine=db_engine)
    s.set_assigned("2026-06", "food", 800)
    s.set_assigned("2026-06", "transport", 200.5)
    assert s.assigned("2026-06", "food") == 800.0
    assert s.month_map("2026-06") == {"food": 800.0, "transport": 200.5}
    assert s.months() == ["2026-06"]


def test_all_map_returns_every_month_and_copy_is_isolated(db_engine):
    s = BudgetAssignmentsStore(engine=db_engine)
    s.set_assigned("2026-05", "food", 100)
    s.set_assigned("2026-06", "food", 200)
    s.set_assigned("2026-06", "transport", 50)
    m = s.all_map()
    assert m == {"2026-05": {"food": 100.0}, "2026-06": {"food": 200.0, "transport": 50.0}}
    # a returned inner dict is a copy — mutating it must not corrupt the store
    m["2026-06"]["food"] = 999
    assert s.assigned("2026-06", "food") == 200.0


def test_negative_amount_is_stored_not_deleted(db_engine):
    """FEB-02 fix (2026-07-04): only an EXACT zero deletes the row — a negative amount
    (the result of moving carried-over money out of an envelope past this month's own
    assigned) is stored as-is, not silently coerced to a delete."""
    s = BudgetAssignmentsStore(engine=db_engine)
    s.set_assigned("2026-06", "food", 800)
    s.set_assigned("2026-06", "food", -500)
    assert s.assigned("2026-06", "food") == -500.0
    assert s.month_map("2026-06") == {"food": -500.0}


def test_set_assigned_rounds_to_two_decimal_places(db_engine):
    s = BudgetAssignmentsStore(engine=db_engine)
    s.set_assigned("2026-06", "food", 12.349)
    assert s.assigned("2026-06", "food") == 12.35


def test_set_assigned_returns_the_rounded_amount(db_engine):
    s = BudgetAssignmentsStore(engine=db_engine)
    assert s.set_assigned("2026-06", "food", 12.349) == 12.35


def test_persistence_across_store_instances(db_engine):
    s1 = BudgetAssignmentsStore(engine=db_engine)
    s1.set_assigned("2026-07", "food", 500)
    s2 = BudgetAssignmentsStore(engine=db_engine)
    assert s2.assigned("2026-07", "food") == 500.0


# ── Concurrency: same-row race (the SELECT-then-INSERT/UPDATE pattern is not atomic
# without the store's own lock — a sharper proof than "many different rows race safely",
# since the DB itself already serializes independent-row writes) ──────────────────────


def test_set_assigned_same_row_concurrent_writes_never_corrupt_or_raise(db_engine):
    s = BudgetAssignmentsStore(engine=db_engine)
    n = 8
    barrier = threading.Barrier(n)
    errors: list[BaseException] = []

    def worker(i: int) -> None:
        barrier.wait()
        try:
            s.set_assigned("2026-06", "food", 100.0 + i)
        except BaseException as exc:  # noqa: BLE001 - captured for the assertion below
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == [], f"concurrent same-row writes raised: {errors}"
    rows = _raw_rows(db_engine)
    assert len(rows) == 1, "the lock must serialize the read-modify-write into ONE surviving row"
    assert rows[0].category == "food"
    assert s.assigned("2026-06", "food") in {100.0 + i for i in range(n)}


# ── Checkpoint coverage + /rewind (db_isolated — the global engine ai.checkpoint reads) ──


def test_set_assigned_update_is_checkpoint_covered_and_rewind_reverts(db_isolated):
    from backend.ai import checkpoint as cp

    store = BudgetAssignmentsStore()
    store.set_assigned("2026-06", "food", 500.0)  # pre-turn baseline, outside any checkpoint

    ctx, token = cp.begin_turn("s1", 0, "assign more to food")
    try:
        store.set_assigned("2026-06", "food", 800.0)
    finally:
        entry = cp.end_turn(ctx, token)

    assert entry is not None
    assert "budget_assignments" in entry["db_tables"]
    assert store.assigned("2026-06", "food") == 800.0

    cp.restore(entry["id"], mode="data")
    assert store.assigned("2026-06", "food") == 500.0


def test_set_assigned_delete_is_checkpoint_covered_and_rewind_reverts(db_isolated):
    """The DELETE-on-zero path is a DIFFERENT write shape than update (before_payload is a
    real row, not None) — restore must RE-INSERT it, proven as its own case."""
    from backend.ai import checkpoint as cp

    store = BudgetAssignmentsStore()
    store.set_assigned("2026-06", "food", 500.0)

    ctx, token = cp.begin_turn("s2", 0, "zero out food")
    try:
        store.set_assigned("2026-06", "food", 0.0)
    finally:
        entry = cp.end_turn(ctx, token)

    assert entry is not None
    assert "budget_assignments" in entry["db_tables"]
    assert store.month_map("2026-06") == {}

    cp.restore(entry["id"], mode="data")
    assert store.assigned("2026-06", "food") == 500.0


def test_set_assigned_composes_with_callers_open_transaction(db_isolated):
    from backend.db.engine import locked_unit_of_work
    from backend.db.tables import budget_assignments as budget_assignments_table

    store = BudgetAssignmentsStore()
    with locked_unit_of_work(budget_assignments_table) as conn:
        store.set_assigned("2026-06", "food", 250.0, conn=conn)
    assert store.assigned("2026-06", "food") == 250.0
