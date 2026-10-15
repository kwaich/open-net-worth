"""PayloadTableStore — the generic JsonStore-surface base (plan 2, task 4).

Every method here is verified against the semantics the now-retired ``JsonStore``
class originally established (a flat-list store: insert/upsert/update_by_id/
bulk_patch/delete/query, in file order) — that class is gone (plan 4e), so this
suite is now the executable spec in its own right, not a cross-check against a
live sibling. The probe table (``payload_probe``) is registered on the
shared ``backend.db.tables.metadata`` from inside the ``probe_table`` fixture, per-test,
and explicitly UNREGISTERED (``metadata.remove(table)``) in that same fixture's
teardown — never left attached at module import time and never left attached past the
one test that used it. This matters because ``pytest`` imports every test module during
COLLECTION, before any test RUNS, and — proven empirically while writing this file —
pytest does NOT always run tests/db in alphabetical order: `pytest tests/db -q` and the
`testpaths=["tests"]` full-suite gate both do (directory scans are sorted), but an
explicit multi-file invocation like `pytest tests/db/test_payload_store.py
tests/db/test_migrations.py` preserves the GIVEN order instead. A table left registered
after this module's tests finish would then make ``test_migrations.py::
test_alembic_check_is_clean`` (same directory, same process) see an extra table with no
migration behind it and fail — reproduced against a pre-teardown version of this
fixture. The create-then-remove cycle makes correctness independent of run order
entirely, not merely lucky under the gate commands this repo happens to use. Plans 3-4's
real subclasses create their tables via an Alembic revision instead, same as
``evidence``/``price_points`` — this create/remove-per-test probe is a TEST-ONLY
one-off, never the pattern for a real store.
"""

from __future__ import annotations

import concurrent.futures

import pytest
from sqlalchemy import func, select

from backend.db.payload_store import PayloadTableStore, payload_table
from backend.db.tables import metadata


@pytest.fixture
def probe_table(db_isolated):
    """Registers + physically creates 'payload_probe' in THIS test's isolated DB (the
    alembic chain has no revision for it, so it is created directly — checkfirst=True),
    then unregisters it from the shared metadata on teardown so no later test — in any
    file, in any order — ever sees it. See the module docstring for why teardown is not
    optional here.

    Registration itself sits OUTSIDE the try/finally — it can't fail half: either a
    Table object now exists on `metadata` (in which case everything after it, including
    `.create()`, must be covered by the `finally`), or `payload_table()` itself raised
    and nothing new was registered for this invocation to clean up. Setup failures must
    also unregister — a leaked registration breaks alembic check process-wide (reviewed
    2026-08-07)."""
    from backend.db.engine import get_engine

    table = payload_table("payload_probe")
    try:
        table.create(get_engine(), checkfirst=True)
        yield table
    finally:
        metadata.remove(table)


@pytest.fixture
def store(probe_table):
    return PayloadTableStore(probe_table)


def _turn(session="s1"):
    from backend.ai import checkpoint as cp

    return cp.begin_turn(session, 0, "test turn")


# ── payload_table() factory shape ────────────────────────────────────────────────


def test_payload_table_factory_shape(probe_table):
    assert probe_table.name == "payload_probe"
    assert [c.name for c in probe_table.columns] == ["seq", "id", "payload"]
    assert probe_table.c.id.primary_key is True
    assert probe_table.c.seq.nullable is False
    assert probe_table.c.payload.nullable is False


# ── _eng() must not cache a lazily-resolved engine (fix round 1, plan 3 task 4) ────
#
# The identical bug found and fixed in all six money stores: PayloadTableStore is a
# long-lived MODULE singleton in real callers (e.g. InstrumentStore() in
# services/activity.py, services/dividend_calendar.py, services/equity.py,
# services/income.py) — constructed once, reused for the process's whole lifetime. If
# the lazily-resolved default engine were cached permanently, an instance built before a
# LATER db_isolated-style redirect (env var + dispose_engine()) would silently keep
# talking to the OLD engine forever, ignoring the redirect entirely.


def test_eng_default_is_never_cached_across_a_later_engine_redirect(store, tmp_path, monkeypatch):
    """engine=None (the default) must be re-resolved via get_engine() on EVERY call —
    never cached after the first."""
    from backend.db import engine as eng

    first = store._eng()
    assert first is eng.get_engine()

    _redirect_engine(tmp_path / "second.db", monkeypatch)
    store._table.create(eng.get_engine(), checkfirst=True)  # test-only probe, no alembic revision

    second = store._eng()
    assert second is eng.get_engine()
    assert second is not first, (
        "engine=None must re-resolve per call — a cached default silently ignores a later redirect"
    )


def test_eng_injected_engine_stays_honored_across_a_later_global_redirect(
    probe_table, tmp_path, monkeypatch
):
    """The other half of the contract: an EXPLICITLY injected engine= (test injection)
    stays pinned even if the process-wide engine singleton is later redirected — an
    injected engine is an explicit caller choice, never silently overridden."""
    from backend.db import engine as eng

    injected = eng.get_engine()
    injected_store = PayloadTableStore(probe_table, engine=injected)

    _redirect_engine(tmp_path / "second.db", monkeypatch)

    assert injected_store._eng() is injected
    assert injected_store._eng() is not eng.get_engine()


def _redirect_engine(db_path, monkeypatch) -> None:
    """Simulates a LATER db_isolated-style redirect happening mid-test — exactly what a
    real module-singleton store would see across two sequential pytest tests sharing one
    process (db_isolated's own mechanism: env var + dispose_engine(), see
    tests/db/conftest.py)."""
    from alembic import command

    from backend.db import engine as eng
    from backend.db.alembic_config import make_config

    url = f"sqlite:///{db_path}"
    monkeypatch.setenv("ONW_DATABASE_URL", url)
    eng.dispose_engine()
    command.upgrade(make_config(url), "head")


# ── Parity suite: one test per JsonStore surface method ──────────────────────────


def test_insert_appends_in_order(store):
    a = store.insert({"id": "a", "v": 1})
    b = store.insert({"id": "b", "v": 2})
    c = store.insert({"id": "c", "v": 3})
    assert (a, b, c) == ({"id": "a", "v": 1}, {"id": "b", "v": 2}, {"id": "c", "v": 3})
    assert store.all() == [{"id": "a", "v": 1}, {"id": "b", "v": 2}, {"id": "c", "v": 3}]


def test_bulk_insert_appends_all_in_one_cycle(store):
    store.insert({"id": "a", "v": 1})
    items = [{"id": "b", "v": 2}, {"id": "c", "v": 3}]
    result = store.bulk_insert(items)
    assert result is items
    assert store.all() == [{"id": "a", "v": 1}, {"id": "b", "v": 2}, {"id": "c", "v": 3}]


def test_bulk_insert_empty_is_noop(store):
    assert store.bulk_insert([]) == []
    assert store.all() == []


def test_find_by_id_found_and_missing(store):
    store.insert({"id": "a", "v": 1})
    assert store.find_by_id("a") == {"id": "a", "v": 1}
    assert store.find_by_id("ghost") is None


def test_upsert_replace_keeps_position_not_append(store):
    store.insert({"id": "a", "v": 1})
    store.insert({"id": "b", "v": 2})
    store.insert({"id": "c", "v": 3})

    replaced = store.upsert({"id": "b", "v": 99})

    assert replaced == {"id": "b", "v": 99}
    assert store.all() == [{"id": "a", "v": 1}, {"id": "b", "v": 99}, {"id": "c", "v": 3}]


def test_upsert_new_id_appends(store):
    store.insert({"id": "a", "v": 1})
    store.upsert({"id": "d", "v": 4})
    assert store.all() == [{"id": "a", "v": 1}, {"id": "d", "v": 4}]


def test_update_by_id_merges_in_place(store):
    store.insert({"id": "a", "v": 1, "tag": "x"})
    store.insert({"id": "b", "v": 2, "tag": "x"})

    updated = store.update_by_id("a", {"v": 100})

    assert updated == {"id": "a", "v": 100, "tag": "x"}
    assert store.all() == [{"id": "a", "v": 100, "tag": "x"}, {"id": "b", "v": 2, "tag": "x"}]


def test_update_by_id_missing_returns_none_and_writes_nothing(store):
    store.insert({"id": "a", "v": 1})
    assert store.update_by_id("ghost", {"v": 2}) is None
    assert store.all() == [{"id": "a", "v": 1}]


def test_bulk_patch_found_ids_only_single_transaction(store):
    store.insert({"id": "a", "v": 1})
    store.insert({"id": "b", "v": 2})
    store.insert({"id": "c", "v": 3})

    result = store.bulk_patch({"a": {"v": 10}, "c": {"v": 30}, "ghost": {"v": 999}})

    assert result == {"a": {"id": "a", "v": 10}, "c": {"id": "c", "v": 30}}
    assert store.all() == [{"id": "a", "v": 10}, {"id": "b", "v": 2}, {"id": "c", "v": 30}]


def test_bulk_patch_empty_updates_is_noop(store):
    store.insert({"id": "a", "v": 1})
    assert store.bulk_patch({}) == {}
    assert store.all() == [{"id": "a", "v": 1}]


def test_delete_returns_bool(store):
    store.insert({"id": "a", "v": 1})
    assert store.delete("a") is True
    assert store.all() == []
    assert store.delete("a") is False


def test_replace_all_wipes_and_renumbers_seq_from_zero(store, probe_table):
    from backend.db.engine import get_engine

    store.insert({"id": "x", "v": 1})
    store.insert({"id": "y", "v": 2})

    result = store.replace_all([{"id": "m", "v": 10}, {"id": "n", "v": 20}, {"id": "o", "v": 30}])

    assert result is None  # matches JsonStore.replace_all's return type exactly
    assert store.all() == [{"id": "m", "v": 10}, {"id": "n", "v": 20}, {"id": "o", "v": 30}]
    with get_engine().connect() as c:
        seqs = [r.seq for r in c.execute(select(probe_table.c.seq).order_by(probe_table.c.seq))]
    assert seqs == [0, 1, 2]


def test_query_equality_and_none_filters_skipped(store):
    store.insert({"id": "a", "tag": "x"})
    store.insert({"id": "b", "tag": "y"})
    store.insert({"id": "c", "tag": "x"})

    assert store.query(tag="x") == [{"id": "a", "tag": "x"}, {"id": "c", "tag": "x"}]
    assert store.query(tag=None) == store.all()  # None means "skip this filter", not "match null"


def test_count_matches_query_length(store):
    store.insert({"id": "a", "tag": "x"})
    store.insert({"id": "b", "tag": "y"})
    store.insert({"id": "c", "tag": "x"})

    assert store.count(tag="x") == 2 == len(store.query(tag="x"))
    assert store.count() == 3


# ── Checkpoint integration (checkpointed=True, the default) ──────────────────────


def test_checkpointed_update_records_before_image_and_restores(store, probe_table):
    from backend.ai import checkpoint as cp
    from backend.db import tables as t
    from backend.db.engine import get_engine

    store.insert({"id": "p1", "name": "Before"})

    ctx, token = _turn()
    store.update_by_id("p1", {"name": "After"})
    entry = cp.end_turn(ctx, token)

    assert entry is not None and "payload_probe" in entry["db_tables"]
    with get_engine().connect() as c:
        row = c.execute(
            select(t.write_log.c.before_payload).where(
                t.write_log.c.checkpoint_id == entry["id"], t.write_log.c.pk == "p1"
            )
        ).first()
    # The FULL row (seq, id, payload) — not just the payload sub-object — since a
    # restore replays it via table.insert().values(**before_payload), which needs
    # every real column to land the row back at its original seq/position.
    assert row.before_payload == {"seq": 0, "id": "p1", "payload": {"id": "p1", "name": "Before"}}

    cp.restore(entry["id"], mode="data")
    assert store.find_by_id("p1") == {"id": "p1", "name": "Before"}


def test_checkpointed_insert_records_none_before_image(store, probe_table):
    from backend.ai import checkpoint as cp
    from backend.db import tables as t
    from backend.db.engine import get_engine

    ctx, token = _turn()
    store.insert({"id": "p2", "name": "New"})
    entry = cp.end_turn(ctx, token)

    assert entry is not None and "payload_probe" in entry["db_tables"]
    with get_engine().connect() as c:
        row = c.execute(
            select(t.write_log.c.before_payload).where(
                t.write_log.c.checkpoint_id == entry["id"], t.write_log.c.pk == "p2"
            )
        ).first()
    assert row.before_payload is None

    cp.restore(entry["id"], mode="data")
    assert store.find_by_id("p2") is None


def test_checkpointed_delete_restores_middle_row_to_original_position(store):
    """Finding 2 (review, 2026-08-07): the two single-row checkpoint tests above can't
    observe seq/position — a restore that reinserted a row at whatever seq is next,
    instead of its ORIGINAL seq, would pass both of them. This is the multi-row proof:
    delete the MIDDLE of three rows inside a turn, restore, and assert the row lands
    back BETWEEN its original neighbors (order, not just membership) with its exact
    original payload — the central claim of the module docstring's CHECKPOINTS
    section, now backed by a committed test instead of only an out-of-band repro."""
    from backend.ai import checkpoint as cp

    store.insert({"id": "a", "v": 1})
    store.insert({"id": "b", "v": 2})
    store.insert({"id": "c", "v": 3})

    ctx, token = _turn()
    store.delete("b")
    entry = cp.end_turn(ctx, token)

    assert entry is not None
    cp.restore(entry["id"], mode="data")

    assert [r["id"] for r in store.all()] == ["a", "b", "c"]
    assert store.find_by_id("b") == {"id": "b", "v": 2}


def test_checkpointed_replace_all_permuted_order_restores_without_collision(store):
    """Plan-2 final review (I2/item 3), reproduced pre-fix: a turn's
    ``replace_all([b, a])`` on a table that had ``[a, b]`` reassigns BOTH rows' seq
    under the new order. The old single-phase, reverse-write_log-seq, per-row
    delete-then-insert replay collided on the table's unique seq index — restoring
    row A back to its original seq while row B (not yet processed) still occupied
    it raised IntegrityError, regardless of which row's image got processed first.
    Two-phase replay (delete every touched pk, THEN insert every before-image)
    makes row order irrelevant and this restores cleanly."""
    from backend.ai import checkpoint as cp

    store.insert({"id": "a", "v": 1})
    store.insert({"id": "b", "v": 2})

    ctx, token = _turn()
    store.replace_all([{"id": "b", "v": 2}, {"id": "a", "v": 1}])
    entry = cp.end_turn(ctx, token)

    assert entry is not None
    cp.restore(entry["id"], mode="data")

    assert store.all() == [{"id": "a", "v": 1}, {"id": "b", "v": 2}], (
        "restore must land both rows back at their ORIGINAL seq/position, not just "
        "with membership intact"
    )


# ── Capture-time single-column-PK guard (I2 PLUS) ─────────────────────────────────


def test_construction_raises_on_composite_pk_when_checkpointed():
    """Capture-time guard: a composite-PK table must be refused at
    PayloadTableStore CONSTRUCTION when checkpointed=True, not discovered only at
    replay time deep inside a restore (ai.checkpoint._single_pk_col's replay-time
    guard). The message mirrors that helper's; checkpointed=False must still
    construct fine since the guard is specific to the checkpoint contract."""
    import pytest
    from sqlalchemy import Column, Integer, Table, Text

    from backend.db.payload_store import PayloadTableStore
    from backend.db.tables import metadata

    composite = Table(
        "payload_probe_composite",
        metadata,
        Column("a", Text, primary_key=True),
        Column("b", Integer, primary_key=True),
    )
    try:
        with pytest.raises(RuntimeError, match="single-column"):
            PayloadTableStore(composite, checkpointed=True)
        PayloadTableStore(composite, checkpointed=False)  # unaffected by the guard
    finally:
        metadata.remove(composite)


# ── checkpointed=False skips the hook call entirely ───────────────────────────────


def test_checkpointed_false_writes_zero_write_log_rows(probe_table):
    from backend.ai import checkpoint as cp
    from backend.db import tables as t
    from backend.db.engine import get_engine

    off_store = PayloadTableStore(probe_table, checkpointed=False)

    ctx, token = _turn()
    off_store.insert({"id": "q1", "name": "New"})
    off_store.update_by_id("q1", {"name": "Changed"})
    off_store.delete("q1")
    entry = cp.end_turn(ctx, token)

    assert entry is None  # no writes ever recorded -> finalise() sees has_writes=False
    with get_engine().connect() as c:
        n = c.execute(
            select(func.count())
            .select_from(t.write_log)
            .where(t.write_log.c.table_name == "payload_probe")
        ).scalar_one()
    assert n == 0


# ── Concurrency: the Plan-1 EvidenceStore lesson, now structural ─────────────────


def test_concurrent_inserts_no_seq_collision(probe_table):
    from backend.db.engine import get_engine

    store_ = PayloadTableStore(probe_table, engine=get_engine())
    n = 16

    def _insert_one(i: int) -> None:
        store_.insert({"id": f"c{i}", "n": i})

    with concurrent.futures.ThreadPoolExecutor(max_workers=n) as pool:
        list(pool.map(_insert_one, range(n)))

    assert len(store_.all()) == n
    with get_engine().connect() as c:
        seqs = sorted(r.seq for r in c.execute(select(probe_table.c.seq)))
    assert seqs == list(range(n))
