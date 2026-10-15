"""StatementStore's B20 async-preview lifecycle: ``set_parse_status``/``parse_progress``
(reused ``status`` column — see ``docs/adr/2026-09-09-statement-parse-status-lives-on-the-record.md``)
and the boot sweep (``sweep_interrupted_parses``) that flips an orphaned ``parsing`` row to
``interrupted`` on restart.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, select

from backend.core.errors import ValidationError
from backend.db.statement_store import StatementStore


@pytest.fixture(autouse=True)
def _align_statement_files_dir_with_data_dir(monkeypatch):
    """Mirrors test_statement_store_db.py's own fixture — see that file's docstring."""
    from backend.core import paths as core_paths_mod
    from backend.core.paths import DATA_DIR

    real_dir = DATA_DIR / "files" / "statements"
    real_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(core_paths_mod, "STATEMENTS_FILES_DIR", real_dir)


def _entry(kind="text"):
    return {"kind": kind}


def test_set_parse_status_writes_status_and_progress(db_isolated):
    store = StatementStore()
    rec = store.create("jan.csv", "text/csv", b"date,amount\n2026-01-01,5.00", _entry())
    snap = {
        "stages": [],
        "started_at": "2026-01-01T00:00:00Z",
        "elapsed_s": 1.0,
        "done": False,
        "error": None,
    }

    updated = store.set_parse_status(rec["id"], "parsing", progress=snap)
    assert updated["status"] == "parsing"
    assert updated["parse_progress"] == snap
    assert store.find_by_id(rec["id"])["status"] == "parsing"

    done_snap = {**snap, "done": True, "elapsed_s": 4.0}
    store.set_parse_status(rec["id"], "ready", progress=done_snap)
    reloaded = store.find_by_id(rec["id"])
    assert reloaded["status"] == "ready"
    assert reloaded["parse_progress"]["done"] is True


def test_set_parse_status_omitting_progress_leaves_the_last_snapshot(db_isolated):
    """The boot sweep's own call — it has no live snapshot to carry, and must not overwrite
    the last one the registry actually persisted with nothing."""
    store = StatementStore()
    rec = store.create("jan.csv", "text/csv", b"date,amount\n2026-01-01,5.00", _entry())
    snap = {"stages": [], "started_at": "t", "elapsed_s": 2.0, "done": False, "error": None}
    store.set_parse_status(rec["id"], "parsing", progress=snap)

    store.set_parse_status(rec["id"], "interrupted")
    reloaded = store.find_by_id(rec["id"])
    assert reloaded["status"] == "interrupted"
    assert reloaded["parse_progress"] == snap


def test_set_parse_status_rejects_an_unknown_status(db_isolated):
    store = StatementStore()
    rec = store.create("jan.csv", "text/csv", b"date,amount\n2026-01-01,5.00", _entry())
    with pytest.raises(ValidationError):
        store.set_parse_status(rec["id"], "not-a-real-status")


def test_has_parsed_rows_is_surfaced_from_the_live_column_not_payload(db_isolated):
    """Code review round B, nit 3: ``_statement_summary`` (import_api) needs
    ``has_parsed_rows`` on the record to implement the ADR's legacy-status rule (a NULL
    ``status`` means "ready" once rows actually parsed). It must reflect the promoted
    COLUMN — same override reasoning as ``status`` in ``_enrich`` — not whatever a
    portable-bundle row's raw ``payload`` happens to carry under that key."""
    store = StatementStore()
    rec = store.create("jan.csv", "text/csv", b"date,amount\n2026-01-01,5.00", _entry())
    assert rec["has_parsed_rows"] is False
    assert store.find_by_id(rec["id"])["has_parsed_rows"] is False

    store.set_parsed_rows(
        rec["id"], [{"date": "2026-01-01", "amount": 5.0, "direction": "debit", "description": "x"}]
    )
    reloaded = store.find_by_id(rec["id"])
    assert reloaded["has_parsed_rows"] is True
    assert any(r["has_parsed_rows"] for r in store.all() if r["id"] == rec["id"])


def test_update_by_id_never_resets_status_to_null(db_isolated):
    """Code review BLOCKER: `update_by_id`'s merge base used to be `_full` (payload-only, no
    `status` key), so `_statement_row` derived the promoted `status` COLUMN from a merge dict
    that never had it — resetting it to NULL on ANY unrelated patch (e.g. `add_batch_id` on
    every commit, or `decision_preview`'s own mid-parse cache write). A `failed` statement
    later touched by an unrelated `update_by_id` (undo_lucius_ops, an instructions save) must
    keep its lifecycle state."""
    store = StatementStore()
    rec = store.create("jan.csv", "text/csv", b"date,amount\n2026-01-01,5.00", _entry())
    store.set_parse_status(rec["id"], "failed", error="boom")

    store.update_by_id(rec["id"], {"account_id": "acct_1"})

    reloaded = store.find_by_id(rec["id"])
    assert reloaded["status"] == "failed"
    assert reloaded["account_id"] == "acct_1"


def test_bulk_patch_never_resets_status_to_null(db_isolated):
    store = StatementStore()
    rec = store.create("jan.csv", "text/csv", b"date,amount\n2026-01-01,5.00", _entry())
    store.set_parse_status(rec["id"], "interrupted")

    store.bulk_patch({rec["id"]: {"account_id": "acct_2"}})

    reloaded = store.find_by_id(rec["id"])
    assert reloaded["status"] == "interrupted"
    assert reloaded["account_id"] == "acct_2"


def test_add_batch_id_never_resets_status_to_null(db_isolated):
    """`add_batch_id` (every decision commit) goes through `update_by_id` — the exact call
    site named in the review finding."""
    store = StatementStore()
    rec = store.create("jan.csv", "text/csv", b"date,amount\n2026-01-01,5.00", _entry())
    store.set_parse_status(rec["id"], "ready", progress={"stages": [], "done": True, "error": None})

    store.add_batch_id(rec["id"], "batch_xyz")

    reloaded = store.find_by_id(rec["id"])
    assert reloaded["status"] == "ready"
    assert reloaded["batch_ids"] == ["batch_xyz"]


def test_sweep_interrupted_parses_flips_only_parsing_rows(db_isolated):
    store = StatementStore()
    parsing = store.create("a.csv", "text/csv", b"a", _entry())
    ready = store.create("b.csv", "text/csv", b"b", _entry())
    failed = store.create("c.csv", "text/csv", b"c", _entry())
    untouched = store.create("d.csv", "text/csv", b"d", _entry())  # status stays NULL

    store.set_parse_status(parsing["id"], "parsing")
    store.set_parse_status(ready["id"], "ready")
    store.set_parse_status(failed["id"], "failed")

    flipped = store.sweep_interrupted_parses()

    assert flipped == 1
    assert store.find_by_id(parsing["id"])["status"] == "interrupted"
    assert store.find_by_id(ready["id"])["status"] == "ready"
    assert store.find_by_id(failed["id"])["status"] == "failed"
    # Never set at all (create() writes no `status` key) — genuinely absent, same shape as
    # `import_meta`'s own documented "absent until a preview runs" contract.
    assert store.find_by_id(untouched["id"]).get("status") is None


def test_sweep_interrupted_parses_is_a_noop_with_nothing_stuck(db_isolated):
    store = StatementStore()
    store.create("a.csv", "text/csv", b"a", _entry())
    assert store.sweep_interrupted_parses() == 0


def test_sweep_flips_a_freshly_updated_parsing_record_too(db_isolated):
    """Live QA against 63784dccb: an earlier revision guarded the sweep against flipping a
    row whose progress looked "recently updated", meant as multi-worker insurance — but the
    shipped server is single-worker, so a `parsing` row at boot is dead BY DEFINITION
    regardless of how recently it last reported progress; the guard's actual effect was
    leaving a real orphan (killed mid-parse, then restarted) stuck `parsing` forever, since
    the sweep is its only path out. No grace window any more — every `parsing` row is swept,
    however recently it moved."""
    store = StatementStore()
    rec = store.create("a.csv", "text/csv", b"a", _entry())
    store.set_parse_status(rec["id"], "parsing", progress={"stages": [], "done": False})

    flipped = store.sweep_interrupted_parses()

    assert flipped == 1
    assert store.find_by_id(rec["id"])["status"] == "interrupted"


def test_statement_parse_progress_is_checkpoint_excluded():
    """L5: a rewind must not resurrect stale read-progress for a parse that has since moved
    on — mirrors price_points'/evidence's/asset_brief's own pin (tests/test_*_checkpoint.py)."""
    from backend.ai.checkpoint import _EXCLUDED_TABLES

    assert "statement_parse_progress" in _EXCLUDED_TABLES


def test_statement_parse_progress_is_not_portable_bridged():
    """L5's own side table is derived, in-process transient state — an export/import
    round-trip has no business carrying it (mirrors `sql_query_log`'s identical pin): it is
    excluded from `BRIDGED` by omission, not an explicit opt-out, so this pin catches the day
    someone adds it by copy-pasting a neighboring entry without re-reading this reasoning."""
    from backend.db import portable_bridge as pb

    assert not any("statement_parse_progress" in key for key in pb.BRIDGED)


def test_deleting_a_statement_cascades_its_parse_progress_row(db_isolated, db_url):
    """`statement_parse_progress.statement_id` is `ON DELETE CASCADE` — a deleted statement
    must never leave an orphaned progress row behind."""
    from backend.db.tables import statement_parse_progress

    store = StatementStore()
    rec = store.create("a.csv", "text/csv", b"a", _entry())
    store.set_parse_status(rec["id"], "parsing", progress={"stages": [], "done": False})

    store.delete_statement(rec["id"])

    with create_engine(db_url).connect() as conn:
        row = conn.execute(
            select(statement_parse_progress).where(
                statement_parse_progress.c.statement_id == rec["id"]
            )
        ).first()
    assert row is None


def test_a_parse_writes_many_progress_upserts_but_at_most_two_statements_meta_writes(
    db_isolated, monkeypatch
):
    """The whole point of the side table (L5): ~10 stage/page reports during one parse must
    cost the SAME two `statements_meta` row writes regardless (None -> parsing -> ready) —
    everything else lands on `statement_parse_progress` instead. Counts via the connection's
    compiled SQL text rather than statement-object introspection — robust across SQLAlchemy
    internals and immune to false positives from the side table's own UPDATE/INSERT."""
    from sqlalchemy import Connection as _Connection

    store = StatementStore()
    rec = store.create("a.csv", "text/csv", b"a", _entry())
    meta_writes = 0

    orig_execute = _Connection.execute

    def _spy(self, statement, *a, **k):
        nonlocal meta_writes
        try:
            sql_text = str(statement)
        except Exception:  # noqa: BLE001
            sql_text = ""
        if sql_text.startswith("UPDATE statements_meta"):
            meta_writes += 1
        return orig_execute(self, statement, *a, **k)

    monkeypatch.setattr(_Connection, "execute", _spy)

    for i in range(10):
        done = i == 9
        store.set_parse_status(
            rec["id"],
            "ready" if done else "parsing",
            progress={"stages": [{"stage": "read", "page": i}], "done": done},
        )

    assert meta_writes <= 2, f"expected <=2 statements_meta UPDATEs, saw {meta_writes}"


def _count_progress_selects(monkeypatch) -> list[int]:
    """Returns a one-element mutable list, incremented on every SELECT against the
    `statement_parse_progress` side table — the counter code review round C's follow-up
    asks for, to prove `all()`/`find_by_hash` no longer issue one such query PER ROW."""
    from sqlalchemy import Connection as _Connection

    counter = [0]
    orig_execute = _Connection.execute

    def _spy(self, statement, *a, **k):
        try:
            sql_text = str(statement)
        except Exception:  # noqa: BLE001
            sql_text = ""
        if "FROM statement_parse_progress" in sql_text:
            counter[0] += 1
        return orig_execute(self, statement, *a, **k)

    monkeypatch.setattr(_Connection, "execute", _spy)
    return counter


def test_all_batches_progress_in_one_query_not_one_per_row(db_isolated, monkeypatch):
    """Code review round C follow-up: `all()` used to call `get_parse_progress` — its own
    SELECT — once per row, an N+1 that also hit every `find_by_hash` call transitively
    (`find_by_hash` used to scan `all()`). Five rows must cost ONE progress query, not five."""
    store = StatementStore()
    for i in range(5):
        rec = store.create(f"{i}.csv", "text/csv", f"row{i}".encode(), _entry())
        store.set_parse_status(rec["id"], "parsing", progress={"stages": [], "done": False})

    counter = _count_progress_selects(monkeypatch)
    rows = store.all()

    assert len(rows) == 5
    assert all(r["parse_progress"] is not None for r in rows)
    assert counter[0] == 1, f"expected exactly 1 batched progress query, saw {counter[0]}"


def test_find_by_hash_issues_no_per_row_progress_query(db_isolated, monkeypatch):
    """Code review round C follow-up: `find_by_hash` used to scan `all()` (N rows -> N
    progress queries) to find ONE match. A direct column lookup costs at most one progress
    query total — for the single match, never for every OTHER row in the table."""
    store = StatementStore()
    target = None
    for i in range(5):
        rec = store.create(f"h{i}.csv", "text/csv", f"hashrow{i}".encode(), _entry())
        store.set_parse_status(rec["id"], "parsing", progress={"stages": [], "done": False})
        if i == 3:
            target = rec

    counter = _count_progress_selects(monkeypatch)
    found = store.find_by_hash(target["file_hash"])

    assert found is not None and found["id"] == target["id"]
    assert counter[0] == 1, f"expected exactly 1 progress query (the one match), saw {counter[0]}"
