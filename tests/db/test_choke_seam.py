"""The choke-point composition seam (storage plan 3, task 1): PayloadTableStore's
`conn=` contract + `backend.db.engine.locked_unit_of_work`'s alphabetical
lock-ordering. STEP 0 of the money-core plan — every later multi-store ledger
choke (accounts + transactions in one transaction) composes through exactly this
mechanism, so it is proven here FIRST, against stand-in probe tables (Task 2
hasn't landed the real `accounts`/`transactions` schema yet — these probes are
named so the alphabetical sort mirrors that real pair: 'probe_accounts' <
'probe_transactions').

Every test that could hang on a real deadlock bug runs its risky section on a
background thread with a bounded `join(timeout=...)` — a regression must fail
loud within seconds, never hang the suite (project hang-safety rule).
"""

from __future__ import annotations

import threading

import pytest


def _turn(session="s1"):
    from backend.ai import checkpoint as cp

    return cp.begin_turn(session, 0, "test turn")


@pytest.fixture
def two_probe_tables(db_isolated):
    """Two throwaway payload tables, registered + physically created for THIS
    test's isolated DB, unregistered from the shared metadata on teardown — same
    lifecycle as test_payload_store.py's `probe_table` fixture, doubled. Named so
    'probe_accounts' sorts before 'probe_transactions', mirroring the real pair's
    alphabetical order."""
    from backend.db.engine import get_engine
    from backend.db.payload_store import payload_table
    from backend.db.tables import metadata

    table_a = payload_table("probe_accounts")
    table_b = payload_table("probe_transactions")
    try:
        table_a.create(get_engine(), checkfirst=True)
        table_b.create(get_engine(), checkfirst=True)
        yield table_a, table_b
    finally:
        metadata.remove(table_a)
        metadata.remove(table_b)


# ── conn= contract: conn=None stays byte-identical ────────────────────────────


def test_conn_none_default_still_locks_and_commits_independently(two_probe_tables):
    """conn=None (the default) must remain byte-identical to pre-task-1 behavior:
    the mutate takes its own lock and opens its own transaction. Every existing
    PayloadTableStore caller passes no conn= at all, which must keep just working."""
    from backend.db.payload_store import PayloadTableStore

    table_a, _table_b = two_probe_tables
    store = PayloadTableStore(table_a)

    store.insert({"id": "x", "v": 1}, conn=None)
    store.upsert({"id": "x", "v": 2}, conn=None)
    assert store.find_by_id("x") == {"id": "x", "v": 2}

    # And the plain, no-kwarg call (every real caller today) is unaffected.
    store.insert({"id": "y", "v": 1})
    assert store.find_by_id("y") == {"id": "y", "v": 1}


# ── conn= contract: a given conn is used AS-IS — no extra lock, no extra txn ──


def test_conn_given_skips_own_lock_reentrant_safe(two_probe_tables):
    """conn= means the CALLER already holds this table's lock (via
    locked_unit_of_work or equivalent) — a mutate that still tried to take its OWN
    per-table lock when given conn= would deadlock immediately, even on a single
    thread (threading.Lock is not reentrant). Isolated on a background thread with
    a bounded join so a regression fails loud in seconds instead of hanging."""
    from backend.db.engine import locked_unit_of_work
    from backend.db.payload_store import PayloadTableStore

    table_a, _table_b = two_probe_tables
    store = PayloadTableStore(table_a)
    errors: list[BaseException] = []

    def _run():
        try:
            with locked_unit_of_work(table_a) as conn:
                store.insert({"id": "x", "v": 1}, conn=conn)
                store.upsert({"id": "x", "v": 2}, conn=conn)
                store.update_by_id("x", {"v": 3}, conn=conn)
        except BaseException as exc:  # pragma: no cover - failure path only
            errors.append(exc)

    t = threading.Thread(target=_run)
    t.start()
    t.join(timeout=10)
    assert not t.is_alive(), "conn= reentrant-deadlocked on its own table lock"
    assert errors == []
    assert store.find_by_id("x") == {"id": "x", "v": 3}


def test_conn_given_rolls_back_with_callers_transaction(two_probe_tables):
    """conn= means the write is part of the CALLER's transaction — proof the
    mutate never opens (or commits) a transaction of its own: if the caller's
    `with locked_unit_of_work(...)` block raises before it exits, the composed
    write must roll back too."""
    from backend.db.engine import locked_unit_of_work
    from backend.db.payload_store import PayloadTableStore

    table_a, _table_b = two_probe_tables
    store = PayloadTableStore(table_a)

    with pytest.raises(RuntimeError, match="simulated failure"):
        with locked_unit_of_work(table_a) as conn:
            store.insert({"id": "x", "v": 1}, conn=conn)
            raise RuntimeError("simulated failure after the composed write")

    assert store.all() == [], "a write made via conn= must roll back with its caller's txn"


def test_conn_composed_write_still_fires_checkpoint_hook(two_probe_tables):
    """A conn=-composed write is not a second-class write: it still fires the
    checkpoint before-image hook through the given connection (same as a plain
    call), so it is still undoable via restore()."""
    from backend.ai import checkpoint as cp
    from backend.db.engine import locked_unit_of_work
    from backend.db.payload_store import PayloadTableStore

    table_a, _table_b = two_probe_tables
    store = PayloadTableStore(table_a)
    store.insert({"id": "x", "v": 1})

    ctx, token = _turn()
    with locked_unit_of_work(table_a) as conn:
        store.update_by_id("x", {"v": 2}, conn=conn)
    entry = cp.end_turn(ctx, token)

    assert entry is not None and "probe_accounts" in entry["db_tables"]
    cp.restore(entry["id"], mode="data")
    assert store.find_by_id("x") == {"id": "x", "v": 1}


# ── locked_unit_of_work: deterministic alphabetical lock-ordering proof ───────


def test_locked_unit_of_work_acquires_alphabetically_regardless_of_arg_order(
    two_probe_tables, monkeypatch, db_isolated
):
    """Direct, non-timing-dependent proof that locked_unit_of_work sorts by table
    NAME before acquiring locks: passing tables in REVERSE name order must still
    acquire them in alphabetical order. threading.Lock instances don't support
    attribute patching (C-level, read-only), so this spies via a fake
    _lock_for_table that hands out simple Python wrapper locks instead."""
    from backend.db import engine as eng

    table_a, table_b = two_probe_tables  # probe_accounts, probe_transactions
    acquired: list[str] = []
    wrappers: dict[str, object] = {}

    class _SpyLock:
        def __init__(self, name: str):
            self._name = name
            self._real = threading.Lock()

        def acquire(self, *a, **kw):
            acquired.append(self._name)
            return self._real.acquire(*a, **kw)

        def release(self, *a, **kw):
            return self._real.release(*a, **kw)

    def _fake_lock_for_table(name: str):
        if name not in wrappers:
            wrappers[name] = _SpyLock(name)
        return wrappers[name]

    monkeypatch.setattr(eng, "_lock_for_table", _fake_lock_for_table)

    with eng.locked_unit_of_work(table_b, table_a):  # reverse name order, on purpose
        pass

    assert acquired == ["probe_accounts", "probe_transactions"]


def test_locked_unit_of_work_duplicate_table_collapses_to_one_acquisition(
    two_probe_tables, db_isolated
):
    """Passing the same table twice must not deadlock the caller against itself
    (threading.Lock is not reentrant) — duplicates collapse before locking."""
    from backend.db.engine import locked_unit_of_work

    table_a, _table_b = two_probe_tables
    errors: list[BaseException] = []

    def _run():
        try:
            with locked_unit_of_work(table_a, table_a):
                pass
        except BaseException as exc:  # pragma: no cover - failure path only
            errors.append(exc)

    t = threading.Thread(target=_run)
    t.start()
    t.join(timeout=10)
    assert not t.is_alive(), "a duplicate table argument self-deadlocked"
    assert errors == []


# ── The mandatory cross-table concurrency test (Plan-2 deferred item) ─────────


def test_locked_unit_of_work_two_threads_opposite_order_never_deadlock(two_probe_tables):
    """The Plan-2 deferred item, now mandatory: two threads composing writes
    across the SAME two tables in OPPOSITE argument order must never deadlock,
    because locked_unit_of_work normalizes lock acquisition to global alphabetical
    order regardless of the order tables are passed in. A `threading.Barrier`
    forces both threads to start together (maximizing real contention instead of
    accidental serialization), and a brief sleep WHILE holding both locks widens
    the window a lock-ordering bug would need to actually deadlock in. Bounded
    joins make a regression fail loud in seconds, never hang the suite."""
    import time

    from backend.db.engine import locked_unit_of_work
    from backend.db.payload_store import PayloadTableStore

    table_a, table_b = two_probe_tables
    store_a = PayloadTableStore(table_a)
    store_b = PayloadTableStore(table_b)

    barrier = threading.Barrier(2)
    order: list[str] = []
    errors: list[BaseException] = []

    def _writer(tables, label):
        try:
            barrier.wait(timeout=10)
            with locked_unit_of_work(*tables) as conn:
                order.append(f"{label}-start")
                time.sleep(0.05)
                store_a.insert({"id": f"{label}-a"}, conn=conn)
                store_b.insert({"id": f"{label}-b"}, conn=conn)
                order.append(f"{label}-end")
        except BaseException as exc:  # pragma: no cover - failure path only
            errors.append(exc)

    t1 = threading.Thread(target=_writer, args=((table_a, table_b), "t1"))
    t2 = threading.Thread(target=_writer, args=((table_b, table_a), "t2"))  # REVERSED
    t1.start()
    t2.start()
    t1.join(timeout=10)
    t2.join(timeout=10)

    assert not t1.is_alive() and not t2.is_alive(), "deadlock: a writer thread never finished"
    assert errors == []
    # Serialized, never interleaved: one writer's whole start..end must not
    # straddle the other's — proof each transaction genuinely held BOTH locks for
    # its entire body, not just one at a time.
    assert order in (
        ["t1-start", "t1-end", "t2-start", "t2-end"],
        ["t2-start", "t2-end", "t1-start", "t1-end"],
    )
    assert {r["id"] for r in store_a.all()} == {"t1-a", "t2-a"}
    assert {r["id"] for r in store_b.all()} == {"t1-b", "t2-b"}


def test_conn_composed_multi_table_write_is_one_checkpoint_restorable_unit(two_probe_tables):
    """The whole point of conn= composition: a turn that writes to TWO tables
    through ONE locked_unit_of_work transaction must checkpoint — and restore —
    as ONE atomic unit, not two independent single-table writes. This is the
    exact seam Plan 3 task 4's real ledger chokes (accounts + transactions) will
    build on."""
    from backend.ai import checkpoint as cp
    from backend.db.engine import locked_unit_of_work
    from backend.db.payload_store import PayloadTableStore

    table_a, table_b = two_probe_tables
    store_a = PayloadTableStore(table_a)
    store_b = PayloadTableStore(table_b)
    store_a.insert({"id": "a1", "v": 1})
    store_b.insert({"id": "b1", "v": 1})

    ctx, token = _turn()
    with locked_unit_of_work(table_a, table_b) as conn:
        store_a.update_by_id("a1", {"v": 2}, conn=conn)
        store_b.update_by_id("b1", {"v": 2}, conn=conn)
    entry = cp.end_turn(ctx, token)

    assert entry is not None
    assert set(entry["db_tables"]) == {"probe_accounts", "probe_transactions"}
    assert store_a.find_by_id("a1") == {"id": "a1", "v": 2}
    assert store_b.find_by_id("b1") == {"id": "b1", "v": 2}

    cp.restore(entry["id"], mode="data")
    assert store_a.find_by_id("a1") == {"id": "a1", "v": 1}
    assert store_b.find_by_id("b1") == {"id": "b1", "v": 1}
