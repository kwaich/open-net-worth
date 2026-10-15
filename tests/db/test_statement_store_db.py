"""StatementStore — DB-backed (plan 3 task 4). Full public-surface parity: create
(hash-dedup + file-bytes write), set_parsed_rows, add_batch_id, delete_statement,
find_by_hash, list_statements, read_file_bytes, plus the inherited-JsonStore CRUD
surface. The parsed_rows extraction/reinlining round-trip (money_io._statement_row /
_reinline_statement) is the store-specific hazard this file focuses extra coverage on:
every read must return parsed_rows INLINE regardless of whether it was ever extracted to
disk, and an UNRELATED patch (e.g. add_batch_id) must never lose a previously-set
has_parsed_rows flag.
"""

from __future__ import annotations

import pytest

from backend.db.statement_store import StatementStore


@pytest.fixture(autouse=True)
def _align_statement_files_dir_with_data_dir(monkeypatch):
    """The suite's global ``_isolate_statement_files`` autouse fixture (tests/conftest.py)
    deliberately points ``STATEMENTS_FILES_DIR`` at a DIFFERENT per-test directory than
    ``DATA_DIR / "statements"`` (dodging a ``FileExistsError`` some other fixtures trip on
    on their own bare ``.mkdir()``) — but ``StatementStore.read_file_bytes``/
    ``delete_statement`` reconstruct a statement's on-disk path via
    ``DATA_DIR / record["file_path"]``, which assumes the two stay in sync (exactly as
    they do in production; unchanged from the pre-task-4 JsonStore-era implementation —
    this file's tests exercise that real assumption directly, so restore it here rather
    than routing around it)."""
    from backend.core import paths as core_paths_mod
    from backend.core.paths import DATA_DIR

    # 2026-08-21: STATEMENTS_FILES_DIR moved under DATA_DIR/files/statements (see
    # docs/adr/2026-08-21-unify-raw-file-storage-across-blob-stores.md) — StatementStore.
    # create() now bakes "files/statements/..." into file_path, so this override must match
    # that, not the pre-move flat "statements/" location. Every consumer of the statements
    # blob directory now reads through backend.core.paths.statements_files_dir() (Fix 3,
    # security review round 2) — patch its ONE real binding.
    real_dir = DATA_DIR / "files" / "statements"
    real_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(core_paths_mod, "STATEMENTS_FILES_DIR", real_dir)


def _entry(kind="text"):
    return {"kind": kind}


def test_create_fresh_statement(db_isolated):
    store = StatementStore()
    rec = store.create("jan.csv", "text/csv", b"date,amount\n2026-01-01,5.00", _entry())
    assert rec["id"].startswith("stmt_")
    assert rec["filename"] == "jan.csv"
    assert rec["size_bytes"] == len(b"date,amount\n2026-01-01,5.00")
    assert rec["parsed_rows"] is None
    assert rec["batch_ids"] == []
    assert store.find_by_id(rec["id"]) == rec


def test_create_dedups_by_hash_and_updates_upload_entry(db_isolated):
    store = StatementStore()
    content = b"same bytes"
    first = store.create("a.csv", "text/csv", content, {"kind": "text", "text": "v1"})
    second = store.create("a-renamed.csv", "text/csv", content, {"kind": "text", "text": "v2"})
    assert second["id"] == first["id"]
    assert store.count() == 1
    assert store.find_by_id(first["id"])["upload_entry"]["text"] == "v2"
    # filename/original metadata from the FIRST create is preserved (dedup only refreshes
    # upload_entry, matching the original JsonStore-era create()).
    assert store.find_by_id(first["id"])["filename"] == "a.csv"


def test_read_file_bytes_round_trips(db_isolated):
    store = StatementStore()
    rec = store.create("a.pdf", "application/pdf", b"%PDF-1.4 fake", {"kind": "pdf_pages"})
    assert store.read_file_bytes(rec["id"]) == b"%PDF-1.4 fake"
    assert store.read_file_bytes("nope") is None


def test_set_parsed_rows_extracts_to_disk_and_reads_back_inline(db_isolated):
    store = StatementStore()
    rec = store.create("a.csv", "text/csv", b"x", _entry())
    rows = [{"date": "2026-01-01", "amount": 5.0, "direction": "debit", "description": "coffee"}]
    updated = store.set_parsed_rows(rec["id"], rows, account_id="acc_1")
    assert updated["parsed_rows"] == rows
    assert updated["account_id"] == "acc_1"
    # find_by_id/all both reinline from disk, transparently.
    assert store.find_by_id(rec["id"])["parsed_rows"] == rows
    assert store.all()[0]["parsed_rows"] == rows

    # The extraction actually happened — the raw DB payload column has NO parsed_rows key.
    from sqlalchemy import select

    from backend.db.engine import get_engine
    from backend.db.tables import statements_meta

    with get_engine().connect() as conn:
        row = conn.execute(select(statements_meta).where(statements_meta.c.id == rec["id"])).one()
    assert row.has_parsed_rows == 1
    assert "parsed_rows" not in row.payload


def test_unrelated_patch_preserves_parsed_rows_path(db_isolated):
    """add_batch_id never mentions parsed_rows — the merge must reconstruct the FULL
    row (reinlined) first, so the promoted has_parsed_rows flag survives untouched."""
    store = StatementStore()
    rec = store.create("a.csv", "text/csv", b"x", _entry())
    rows = [{"date": "2026-01-01", "amount": 1.0, "direction": "debit", "description": "x"}]
    store.set_parsed_rows(rec["id"], rows)
    store.add_batch_id(rec["id"], "imp_1")
    fetched = store.find_by_id(rec["id"])
    assert fetched["batch_ids"] == ["imp_1"]
    assert fetched["parsed_rows"] == rows  # NOT lost


def test_add_batch_id_dedupes(db_isolated):
    store = StatementStore()
    rec = store.create("a.csv", "text/csv", b"x", _entry())
    store.add_batch_id(rec["id"], "imp_1")
    store.add_batch_id(rec["id"], "imp_1")
    store.add_batch_id(rec["id"], "imp_2")
    assert store.find_by_id(rec["id"])["batch_ids"] == ["imp_1", "imp_2"]


def test_add_batch_id_missing_statement_returns_none(db_isolated):
    assert StatementStore().add_batch_id("nope", "imp_1") is None


def test_find_by_hash(db_isolated):
    store = StatementStore()
    rec = store.create("a.csv", "text/csv", b"unique bytes", _entry())
    found = store.find_by_hash(rec["file_hash"])
    assert found["id"] == rec["id"]
    assert store.find_by_hash("0" * 64) is None


def test_list_statements_sorted_newest_first(db_isolated):
    store = StatementStore()
    a = store.create("a.csv", "text/csv", b"aaa", _entry())
    b = store.create("b.csv", "text/csv", b"bbb", _entry())
    store.update_by_id(a["id"], {"created_at": "2026-01-01T00:00:00Z"})
    store.update_by_id(b["id"], {"created_at": "2026-01-02T00:00:00Z"})
    listed = store.list_statements()
    assert [s["id"] for s in listed] == [b["id"], a["id"]]


def test_delete_statement_removes_record_and_file(db_isolated):
    store = StatementStore()
    rec = store.create("a.csv", "text/csv", b"bytes", _entry())
    from backend.core.paths import DATA_DIR

    disk_path = DATA_DIR / rec["file_path"]
    assert disk_path.exists()
    assert store.delete_statement(rec["id"]) is True
    assert store.find_by_id(rec["id"]) is None
    assert not disk_path.exists()
    assert store.delete_statement(rec["id"]) is False  # already gone


def test_delete_statement_also_removes_parsed_rows_sidecar(db_isolated):
    store = StatementStore()
    rec = store.create("a.csv", "text/csv", b"bytes", _entry())
    store.set_parsed_rows(rec["id"], [{"date": "2026-01-01", "amount": 1.0}])
    from backend.core.paths import statements_files_dir

    sidecar = statements_files_dir() / f"{rec['id']}.parsed.json"
    assert sidecar.exists()
    store.delete_statement(rec["id"])
    assert not sidecar.exists()


def test_delete_statement_removes_raw_file_sidecar_and_trail_in_one_op(db_isolated):
    """RFC §6 test case (a): a delete must remove the raw file, the ``.parsed.json``
    sidecar, AND any migration-trail-shaped residue sitting under the id's glob — all in
    one operation, proven together rather than only the two shapes existing tests already
    covered separately. ``unlink_all_blobs``'s own docstring claims it also catches "any
    migration trail (a revision-0046 ``.migrated-<ts>`` rename)" via its unscoped
    ``<id>.*`` glob — this seeds one directly to prove that claim rather than merely
    asserting it."""
    from backend.core.paths import statements_files_dir

    store = StatementStore()
    rec = store.create("a.csv", "text/csv", b"bytes", _entry())
    store.set_parsed_rows(rec["id"], [{"date": "2026-01-01", "amount": 1.0}])
    files_dir = statements_files_dir()
    raw_path = files_dir / rec["file_path"].rsplit("/", 1)[-1]
    sidecar_path = files_dir / f"{rec['id']}.parsed.json"
    trail_path = files_dir / f"{rec['id']}.parsed.json.migrated-20260101T000000Z"
    trail_path.write_text("[]")

    assert raw_path.exists()
    assert sidecar_path.exists()
    assert trail_path.exists()

    assert store.delete_statement(rec["id"]) is True

    assert not raw_path.exists()
    assert not sidecar_path.exists()
    assert not trail_path.exists()


def test_two_different_statement_ids_can_never_collide_on_the_same_sidecar(db_isolated):
    """RFC §6 test case (b): the ``.parsed.json`` collision hazard — two different ids
    somehow producing the SAME sidecar filename. Pinned here: ``id`` is the table's
    PRIMARY KEY, so two rows can never legitimately share one — a second ``create()``/
    insert with a duplicate id is refused at the database layer before either row's
    sidecar path is even derived, making the collision structurally impossible, not merely
    untested."""
    import sqlalchemy.exc

    store = StatementStore()
    rec = store.create("a.csv", "text/csv", b"first bytes", _entry())

    with pytest.raises(sqlalchemy.exc.IntegrityError):
        store.insert({**rec, "id": rec["id"], "file_hash": "different-hash-forces-new-insert"})


def test_delete_statement_unlinks_a_noncanonical_but_contained_file_path(db_isolated):
    """Security review fix round 2, Fix 5: a row whose ``file_path`` was never rewritten
    onto the canonical ``<id><ext>`` shape (revision 0045's own
    ``_rewrite_old_relative_path`` deliberately leaves an unrecognised value alone; a
    portable-bundle import copies ``file_path`` verbatim from untrusted JSON) has a raw
    file the id-based glob in ``unlink_all_blobs`` can never find, since it isn't named
    ``<statement_id>.*`` at all — ``delete_statement`` must still remove it when the
    resolved path stays INSIDE ``statements_files_dir()``."""
    from backend.core.paths import statements_files_dir

    store = StatementStore()
    rec = store.create("a.csv", "text/csv", b"bytes", _entry())
    files_dir = statements_files_dir()
    noncanonical = files_dir / "legacy_named_file.csv"
    noncanonical.write_bytes(b"legacy bytes")
    store.update_by_id(rec["id"], {"file_path": f"files/statements/{noncanonical.name}"})

    assert store.delete_statement(rec["id"]) is True
    assert not noncanonical.exists()


def test_delete_statement_never_unlinks_a_file_path_outside_statements_files_dir(
    db_isolated, caplog
):
    """The other half of Fix 5: a ``file_path`` that resolves OUTSIDE
    ``statements_files_dir()`` is untrusted (a portable-bundle import copies it verbatim) —
    deleting it blindly would be an ARBITRARY-UNLINK primitive, the exact worse bug the
    pre-DB-migration unconditional unlink used to be. It must be left alone, and logged as
    an orphan needing manual attention — a discoverable leak, never a silent one, and never
    a delete of something this store cannot prove it owns."""
    import logging

    from backend.core.paths import DATA_DIR

    store = StatementStore()
    rec = store.create("a.csv", "text/csv", b"bytes", _entry())
    outside = DATA_DIR.parent / "outside_the_statements_store.csv"
    outside.write_bytes(b"do not touch me")
    try:
        store.update_by_id(rec["id"], {"file_path": "../outside_the_statements_store.csv"})

        with caplog.at_level(logging.WARNING, logger="backend.db.statement_store"):
            assert store.delete_statement(rec["id"]) is True

        assert outside.exists()
        assert outside.read_bytes() == b"do not touch me"
        assert any("outside" in r.message.lower() for r in caplog.records)
    finally:
        outside.unlink(missing_ok=True)


def test_delete_statement_defers_blob_unlink_when_conn_is_given(db_isolated):
    """Security review fix round 2, Fix 7 — mirrors ``ReceiptStore.delete_receipt``'s own
    documented rollback-safety contract exactly: when ``conn`` is given, this call is
    composed inside a CALLER's own transaction. A filesystem unlink is not part of that SQL
    transaction and is never undone by a rollback, so blob removal must be DEFERRED — the
    row is gone from the DB, but the raw file and sidecar are still on disk immediately
    after the call returns, and only actually go away once the caller unlinks them itself
    (mirroring the real caller shape ``services.receipts._delete`` uses)."""
    from backend.core.paths import statements_files_dir
    from backend.db.engine import unit_of_work

    store = StatementStore()
    rec = store.create("a.csv", "text/csv", b"bytes", _entry())
    store.set_parsed_rows(rec["id"], [{"date": "2026-01-01", "amount": 1.0}])
    files_dir = statements_files_dir()
    sidecar_path = files_dir / f"{rec['id']}.parsed.json"
    assert sidecar_path.exists()

    with unit_of_work() as conn:
        assert store.delete_statement(rec["id"], conn=conn) is True
        # Still on disk INSIDE the transaction — deferred, not deleted yet.
        assert sidecar_path.exists()

    # The DB row is gone...
    assert store.find_by_id(rec["id"]) is None
    # ...but the blob survives the commit too — deferred means the CALLER must clean it up.
    assert sidecar_path.exists()

    store.unlink_all_blobs(rec["id"], file_path=rec["file_path"])
    assert not sidecar_path.exists()


def test_delete_statement_missing_returns_false(db_isolated):
    assert StatementStore().delete_statement("nope") is False


def test_generic_crud_surface(db_isolated):
    store = StatementStore()
    rec = store.create("a.csv", "text/csv", b"x", _entry())
    assert store.count() == 1
    updated = store.update_by_id(rec["id"], {"filename": "renamed.csv"})
    assert updated["filename"] == "renamed.csv"
    assert store.delete(rec["id"]) is True


def test_upsert_wholesale_replace_not_merge(db_isolated):
    """upsert replaces wholesale with the caller's own item — unlike update_by_id, it
    does NOT merge onto the reconstructed existing row."""
    store = StatementStore()
    rec = store.create("a.csv", "text/csv", b"x", _entry())
    store.set_parsed_rows(rec["id"], [{"date": "2026-01-01", "amount": 1.0}])
    replacement = {**store.find_by_id(rec["id"])}
    del replacement["parsed_rows"]  # the caller's own item explicitly omits it
    replacement["filename"] = "b.csv"
    store.upsert(replacement)
    fetched = store.find_by_id(rec["id"])
    assert fetched["filename"] == "b.csv"
    assert "parsed_rows" not in fetched


def test_write_fires_checkpoint_hook_and_restores(db_isolated):
    from backend.ai import checkpoint as cp

    store = StatementStore()
    ctx, token = cp.begin_turn("s1", 0, "t")
    try:
        rec = store.create("a.csv", "text/csv", b"x", _entry())
    finally:
        entry = cp.end_turn(ctx, token)
    assert entry is not None
    assert "statements_meta" in entry["db_tables"]
    cp.restore(entry["id"], mode="data")
    assert store.find_by_id(rec["id"]) is None
