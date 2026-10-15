"""Regression for BUD-SWEEP-01 (2026-07-04): BudgetAssignmentsStore.set_assigned did an
UNLOCKED read-modify-write of the whole file, so concurrent assigns (the auto-assign
fan-out, a Lucius turn racing the UI) were last-writer-wins — writes silently vanished.

DB-backed now (plan 4a task 4): each (month, category) is its own row, so distinct
categories no longer share a "whole blob" to clobber the way the JsonStore-era file did —
this test's own concurrency guarantee (no lost writes across many DIFFERENT categories) is
structurally true regardless of the store's own lock now. Kept as a smoke test for that;
tests/db/test_budget_assignments_store.py::test_set_assigned_same_row_concurrent_writes_never_corrupt_or_raise
is the sharper proof the per-table lock is still load-bearing (the SAME row, which the
store's own SELECT-then-INSERT/UPDATE pattern is not atomic without it)."""

from __future__ import annotations

import threading

from backend.db.budget_assignments_store import BudgetAssignmentsStore


def test_concurrent_set_assigned_loses_no_writes(db_isolated):
    store = BudgetAssignmentsStore()

    n = 8
    barrier = threading.Barrier(n)

    def worker(i: int) -> None:
        barrier.wait()
        store.set_assigned("2026-07", f"cat_{i}", 100.0 + i)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    month = store.month_map("2026-07")
    assert len(month) == n, f"lost writes: {sorted(month)}"
    for i in range(n):
        assert month[f"cat_{i}"] == 100.0 + i
