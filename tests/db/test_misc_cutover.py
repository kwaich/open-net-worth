"""Misc cutover (plan 4e, task 1): revision 0018 absorbs the 4 misc JSON sources
(artifacts, uploads, email_snapshots, pending_pushes) into their Tier-2 tables (built by
revision 0017), one all-or-nothing unit. ``checkpoints`` — the 5th Wave-E table, also
built by 0017 — is schema-only here; its absorb is revision 0019 (plan 4e task 2), not
this one.

Mirrors ``tests/db/test_logs_cutover.py``'s shape: no cross-file consistency pre-check
(no FKs between these 4 tables, no book_id anywhere in this wave) and no dict-shaped
bespoke store (unlike configs' namespaced settings / dashboard_layout). Covers: exact
round-trip per store (direct ``misc_io`` calls, fast) AND through the real migration
chain (monkeypatched sources + ``command.upgrade``); full absorb + 4 ``json_cutovers``
rows + 4 renames; the up/down/up cycle.

NO MONEY GUARD (``misc_io.py``'s own module docstring) — every fixture below is
verbatim opaque payload; nothing here feeds net worth.

ORDER-SENSITIVE BY CONSTRUCTION even though none of the four is a prepend/ring source
(``misc_io.py``'s own docstring) — the round-trip check below is plain ``==`` on a
parsed JSON list, positional/element-by-element, so a fixture with a NON-sorted,
multi-row order (see ``UPLOADS``) still proves position is preserved exactly, not just
membership.
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import func, select

from backend.db import misc_io as mio
from backend.db.tables import (
    artifacts,
    attachments,
    checkpoints,
    email_snapshots,
    json_cutovers,
    pending_pushes,
)

# The json_cutovers.store VALUE stays "uploads" — that's what revision 0018 (frozen,
# historical) literally stamps; the table it absorbs INTO was renamed "attachments" only
# afterward, at revision 0041 (cross-channel-attachments RFC Phase 2). Post-head queries
# below therefore use the CURRENT `attachments` table object while historical/cutover-
# ledger assertions keep the string "uploads".
MISC_STORE_NAMES = ("artifacts", "uploads", "email_snapshots", "pending_pushes")


def _set_database_url_for_test(url: str, monkeypatch) -> None:
    """Postgres's ``db_url`` is the ONE shared CI server: scrub the public schema first
    so an earlier test's already-applied migration history doesn't make THIS test's
    ``command.upgrade(..., "head")`` a silent no-op. Mirrors every prior wave's own
    identical helper verbatim (e.g. ``test_logs_cutover.py``)."""
    if url.startswith("postgresql"):
        from sqlalchemy import create_engine, text

        scrub = create_engine(url)
        with scrub.begin() as c:
            c.execute(text("DROP SCHEMA public CASCADE"))
            c.execute(text("CREATE SCHEMA public"))
        scrub.dispose()
    monkeypatch.setenv("ONW_DATABASE_URL", url)


# ── Fixtures — deliberately small but non-trivial (unicode, nulls, out-of-order rows —
# the round-trip proof needs a diff to actually be possible) ────────────────────────

ARTIFACTS = [
    {
        "id": "art_1",
        "created_at": "2026-08-01T09:00:00Z",
        "session_id": "sess_1",
        "title": "May 2026 spending — café ☕ unicode round-trip",
        "filename": "may_2026.html",
        "artifact_type": "html",
        "size_bytes": 1234,
        "file_path": "artifacts/art_1.html",
    },
    {
        "id": "art_2",
        "created_at": "2026-08-02T09:00:00Z",
        "session_id": None,
        "title": "Net worth pie",
        "filename": "networth.svg",
        "artifact_type": "svg",
        "size_bytes": 88,
        "file_path": "artifacts/art_2.svg",
    },
]

# Out-of-order on disk (newest first) to prove the round-trip is POSITIONAL, not just
# a membership check — artifacts/uploads/email_snapshots/pending_pushes are all plain
# append-order stores in production, but the cutover's own round-trip guard must not
# silently pass on a source file that happens to already be sorted.
UPLOADS = [
    {
        "id": "up_2",
        "created_at": "2026-08-02T00:00:00Z",
        "filename": "second.xlsx",
        "kind": "text",
        "summary": "Excel file 'second.xlsx' with 3 rows",
        "size_bytes": 512,
        "format": "xlsx",
        "statement_id": None,
        "last_used_at": None,
        "last_used_session": None,
    },
    {
        "id": "up_1",
        "created_at": "2026-08-01T00:00:00Z",
        "filename": "first.csv",
        "kind": "text",
        "summary": "CSV file 'first.csv' with 1 rows",
        "size_bytes": 64,
        "format": "csv",
        "statement_id": "st_1",
        "last_used_at": "2026-08-01T01:00:00Z",
        "last_used_session": "sess_9",
    },
]

EMAIL_SNAPSHOTS = [
    {
        "message_id": "<m-1@x>",
        "subject": "Your statement is ready",
        "from_addr": "alerts@bank.example",
        "received_at": "2026-08-01",
        "body_text": "Amount: $50.00 charged to your card.",
        "referenced": True,
        "stashed_at": "2026-08-01T00:00:00Z",
    },
    {
        "message_id": "<m-2@x>",
        "subject": "Second notice",
        "from_addr": "alerts@bank.example",
        "received_at": "2026-08-02",
        "body_text": "Amount: $12.00 charged — café ☕ unicode round-trip.",
        "referenced": False,
        "stashed_at": "2026-08-02T00:00:00Z",
    },
]

PENDING_PUSHES = [
    {
        "id": "ppush_1",
        "message": "Analysis — AAPL",
        "channels": ["telegram"],
        "label": "Apple",
        "ticker": "AAPL",
        "attempts": 1,
        "next_attempt_at": "2026-08-01T00:05:00+00:00",
        "created_at": "2026-08-01T00:00:00+00:00",
    }
]

# (store name, table, fixture, id_field) — the 4 generic stores. These run against
# POST-HEAD schema (db_engine upgrades to head), so the table is the CURRENT
# `attachments` — not the historical "uploads" name revision 0018 absorbed into.
_GENERIC_SPECS = [
    ("artifacts", artifacts, ARTIFACTS, "id"),
    ("attachments", attachments, UPLOADS, "id"),
    ("email_snapshots", email_snapshots, EMAIL_SNAPSHOTS, "message_id"),
    ("pending_pushes", pending_pushes, PENDING_PUSHES, "id"),
]


def _patch_all_sources_missing(monkeypatch, tmp_path) -> None:
    """Point every cutover source seam — not just this wave's own four — at a file that
    does not exist, so a test exercising the real migration chain never accidentally
    absorbs whatever the ambient (session-shared) DATA_DIR happens to hold. Delegates to
    the canonical cross-wave helper (``tests/db/conftest.py``)."""
    from tests.db.conftest import patch_all_cutover_sources_missing

    patch_all_cutover_sources_missing(monkeypatch, tmp_path)


def _write_all_four_fixtures(tmp_path, monkeypatch) -> dict[str, tuple]:
    """Write every store's fixture to its own file under ``tmp_path`` and monkeypatch the
    matching ``versions_evidence_source`` constant at it. Returns ``{attr: (filename,
    original)}`` for assertion convenience."""
    files = {
        "ARTIFACTS_SOURCE": ("artifacts.json", ARTIFACTS),
        "UPLOADS_SOURCE": ("uploads.json", UPLOADS),
        "EMAIL_SNAPSHOTS_SOURCE": ("email_snapshots.json", EMAIL_SNAPSHOTS),
        "PENDING_PUSHES_SOURCE": ("pending_pushes.json", PENDING_PUSHES),
    }
    for attr, (filename, fixture) in files.items():
        path = tmp_path / filename
        path.write_text(json.dumps(fixture, ensure_ascii=False))
        monkeypatch.setattr(f"backend.db.alembic.versions_evidence_source.{attr}", path)
    return files


# ── Direct round-trip per store (fixture-scale, no migration chain needed) ──────────


@pytest.mark.parametrize(
    "store,table,fixture,id_field", _GENERIC_SPECS, ids=[spec[0] for spec in _GENERIC_SPECS]
)
def test_each_store_round_trips_exactly(db_engine, store, table, fixture, id_field):
    with db_engine.begin() as conn:
        n = mio.import_payload_list(conn, table, fixture, id_field=id_field)
    assert n == len(fixture)
    with db_engine.connect() as conn:
        assert mio.export_payload_list(conn, table) == fixture


def test_no_book_id_column_on_any_of_the_four(db_engine):
    with db_engine.begin() as conn:
        mio.import_payload_list(conn, pending_pushes, PENDING_PUSHES)
    with db_engine.connect() as conn:
        row = conn.execute(select(pending_pushes).where(pending_pushes.c.id == "ppush_1")).one()
    assert not hasattr(row, "book_id")
    assert row.seq == 0


def test_email_snapshots_keyed_by_message_id_key_stays_in_payload(db_engine):
    with db_engine.begin() as conn:
        mio.import_payload_list(conn, email_snapshots, EMAIL_SNAPSHOTS, id_field="message_id")
    with db_engine.connect() as conn:
        row = conn.execute(select(email_snapshots).where(email_snapshots.c.id == "<m-1@x>")).one()
    assert row.payload["message_id"] == "<m-1@x>"
    assert "id" not in row.payload


def test_missing_source_files_are_a_clean_empty_export_for_every_store(db_engine):
    with db_engine.connect() as conn:
        for _store, table, _fixture, _id_field in _GENERIC_SPECS:
            assert mio.export_payload_list(conn, table) == []


# ── MISC_PAYLOAD_SPECS / MISC_FILES constants ────────────────────────────────────


def test_misc_payload_specs_covers_the_four_stores():
    specs = {fn: (table, id_field) for fn, table, id_field in mio.MISC_PAYLOAD_SPECS}
    assert set(specs) == {
        "artifacts.json",
        "attachments.json",
        "email_snapshots.json",
        "pending_pushes.json",
    }
    assert specs["email_snapshots.json"] == (email_snapshots, "message_id")
    for fn, (_table, id_field) in specs.items():
        if fn != "email_snapshots.json":
            assert id_field == "id"


def test_misc_files_lists_all_four():
    assert set(mio.MISC_FILES) == {
        "artifacts.json",
        "attachments.json",
        "email_snapshots.json",
        "pending_pushes.json",
    }
    assert len(mio.MISC_FILES) == 4


# ── checkpoints: schema-only this task — the table exists, but nothing populates it ────


def test_checkpoints_table_exists_but_empty_after_head(db_engine):
    with db_engine.connect() as conn:
        count = conn.execute(select(func.count()).select_from(checkpoints)).scalar_one()
    assert count == 0


# ── Full migration chain: monkeypatched sources + command.upgrade(head) ─────────────


def test_all_four_stores_round_trip_exactly_through_the_real_migration(
    tmp_path, db_url, monkeypatch
):
    files = _write_all_four_fixtures(tmp_path, monkeypatch)
    _set_database_url_for_test(db_url, monkeypatch)

    from alembic import command

    from backend.db import engine as eng
    from backend.db.alembic_config import make_config

    eng.dispose_engine()
    command.upgrade(make_config(db_url), "head")

    exporters = {
        "ARTIFACTS_SOURCE": lambda c: mio.export_payload_list(c, artifacts),
        # Post-head: revision 0041 has already renamed "uploads" -> "attachments".
        "UPLOADS_SOURCE": lambda c: mio.export_payload_list(c, attachments),
        "EMAIL_SNAPSHOTS_SOURCE": lambda c: mio.export_payload_list(c, email_snapshots),
        "PENDING_PUSHES_SOURCE": lambda c: mio.export_payload_list(c, pending_pushes),
    }
    # Revision 0041 (chained here since this upgrades to "head") backfills six new
    # provenance fields onto every row 0018 absorbs into "uploads"/attachments — so ITS
    # round-trip is "original + those defaults", not byte-identical. Every other store is
    # untouched by 0041 and stays byte-identical.
    _attachments_backfill = {
        "media_type": None,
        "content_hash": None,
        "blob_locator": None,
        "source_channel": "legacy:unknown",
        "source_message_id": None,
        "source_conversation_id": None,
        "metadata": {},
    }
    with eng.get_engine().connect() as conn:
        for attr, (_filename, original) in files.items():
            if attr == "UPLOADS_SOURCE":
                expected = [{**row, **_attachments_backfill} for row in original]
            elif attr == "ARTIFACTS_SOURCE":
                # Revision 0045 (unify-raw-file-storage, chained here since this upgrades
                # to "head") rewrites every pre-existing artifact's file_path from the
                # legacy "artifacts/<name>" form to "files/artifacts/<name>" as part of
                # this same from-scratch chain — a real, intentional correction (same
                # rewrite step test_money_cutover.py's statements case already accounts
                # for), not a round-trip bug.
                expected = [{**row, "file_path": f"files/{row['file_path']}"} for row in original]
            else:
                expected = original
            assert exporters[attr](conn) == expected, f"{attr} did not round-trip exactly"

    eng.dispose_engine()


def test_one_store_absent_is_a_clean_skip_other_three_still_absorb(tmp_path, db_url, monkeypatch):
    """Any one store may be absent (e.g. a fresh install that never wrote
    pending_pushes.json — the retry queue only exists once a zero-delivery push fires) —
    absorbed only if present; the other three still absorb normally."""
    _write_all_four_fixtures(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "backend.db.alembic.versions_evidence_source.PENDING_PUSHES_SOURCE",
        tmp_path / "does_not_exist_pending_pushes.json",
    )
    _set_database_url_for_test(db_url, monkeypatch)

    from alembic import command

    from backend.db import engine as eng
    from backend.db.alembic_config import make_config

    eng.dispose_engine()
    command.upgrade(make_config(db_url), "head")

    with eng.get_engine().connect() as conn:
        assert mio.export_payload_list(conn, pending_pushes) == []
        # See test_all_four_stores_round_trip_exactly_through_the_real_migration's own
        # ARTIFACTS_SOURCE comment — revision 0045's file_path rewrite applies here too.
        expected_artifacts = [
            {**row, "file_path": f"files/{row['file_path']}"} for row in ARTIFACTS
        ]
        assert mio.export_payload_list(conn, artifacts) == expected_artifacts
        count = conn.execute(
            select(func.count())
            .select_from(json_cutovers)
            .where(json_cutovers.c.store == "pending_pushes")
        ).scalar_one()
    assert count == 0
    eng.dispose_engine()


def test_full_migration_is_a_clean_empty_absorb_when_all_four_sources_missing(
    tmp_path, db_url, monkeypatch
):
    """Fresh install: none of the four misc files exist — head is reached, zero rows,
    zero cutover-ledger entries for the misc stores."""
    _patch_all_sources_missing(monkeypatch, tmp_path)
    monkeypatch.setattr("backend.db.migrate.BACKUP_DIR", tmp_path / "backups" / "db")
    _set_database_url_for_test(db_url, monkeypatch)

    from backend.db import engine as eng

    eng.dispose_engine()
    from backend.db.migrate import ensure_migrated

    ensure_migrated()

    with eng.get_engine().connect() as conn:
        for _store, table, _fixture, _id_field in _GENERIC_SPECS:
            assert mio.export_payload_list(conn, table) == []
        count = conn.execute(
            select(func.count())
            .select_from(json_cutovers)
            .where(json_cutovers.c.store.in_(MISC_STORE_NAMES))
        ).scalar_one()
    assert count == 0
    eng.dispose_engine()


def test_wrong_top_level_shape_aborts_the_whole_migration(tmp_path, db_url, monkeypatch):
    _patch_all_sources_missing(monkeypatch, tmp_path)
    path = tmp_path / "artifacts.json"
    path.write_text(json.dumps({"not": "a list"}))
    monkeypatch.setattr("backend.db.alembic.versions_evidence_source.ARTIFACTS_SOURCE", path)
    _set_database_url_for_test(db_url, monkeypatch)

    from alembic import command

    from backend.db import engine as eng
    from backend.db.alembic_config import make_config

    eng.dispose_engine()
    with pytest.raises(RuntimeError):
        command.upgrade(make_config(db_url), "head")
    eng.dispose_engine()


def test_export_mismatch_after_import_aborts_the_whole_migration(tmp_path, db_url, monkeypatch):
    """``_absorb_one``'s OTHER raise branch — ``export_fn(conn) != original`` AFTER a
    successful import — proven on ``pending_pushes`` by monkeypatching the SHARED generic
    ``export_payload_list``: the WHOLE migration must still RAISE (fail-closed,
    all-or-nothing), not silently accept the drifted store."""
    _patch_all_sources_missing(monkeypatch, tmp_path)
    original = list(PENDING_PUSHES)
    path = tmp_path / "pending_pushes.json"
    path.write_text(json.dumps(original, ensure_ascii=False))
    monkeypatch.setattr("backend.db.alembic.versions_evidence_source.PENDING_PUSHES_SOURCE", path)
    monkeypatch.setattr(mio, "export_payload_list", lambda conn, table: [{"drifted": True}])
    _set_database_url_for_test(db_url, monkeypatch)

    from alembic import command

    from backend.db import engine as eng
    from backend.db.alembic_config import make_config

    eng.dispose_engine()
    with pytest.raises(RuntimeError, match="round-trip verification FAILED") as exc_info:
        command.upgrade(make_config(db_url), "head")
    assert "pending_pushes" in str(exc_info.value)
    eng.dispose_engine()


def test_full_migration_absorbs_all_four_and_renames(tmp_path, db_url, monkeypatch):
    files = _write_all_four_fixtures(tmp_path, monkeypatch)
    monkeypatch.setattr("backend.db.migrate.BACKUP_DIR", tmp_path / "backups" / "db")
    _set_database_url_for_test(db_url, monkeypatch)

    from backend.db import engine as eng

    eng.dispose_engine()
    from backend.db.migrate import ensure_migrated

    ensure_migrated()

    for attr, (filename, _original) in files.items():
        src = tmp_path / filename
        assert not src.exists(), f"{attr}'s source must be renamed away, not left in place"
        assert len(list(tmp_path.glob(f"{filename}.migrated-*"))) == 1

    with eng.get_engine().connect() as conn:
        rows = {r.store: r for r in conn.execute(select(json_cutovers))}
    for store in MISC_STORE_NAMES:
        assert rows[store].renamed == 1
        assert rows[store].source_hash

    eng.dispose_engine()


# ── Up/down/up cycle ──────────────────────────────────────────────────────────────────


def test_up_down_up_cycle_is_reversible(db_url, tmp_path, monkeypatch):
    """Structural reversibility of the SCHEMA CHAIN, isolated from data-absorption
    correctness (already fully proven above): every one of the four sources is
    explicitly pointed at a nonexistent file, so both upgrade passes are clean empty
    absorbs — the down step (to 0016, dropping the 5 Wave-E tables via 0017's own
    downgrade; revision 0018's OWN downgrade() is a documented no-op) has nothing to
    lose, and re-upgrading cannot collide on a stale ``json_cutovers`` primary key from
    a first pass that inserted zero rows."""
    _patch_all_sources_missing(monkeypatch, tmp_path)
    _set_database_url_for_test(db_url, monkeypatch)

    from alembic import command
    from sqlalchemy import create_engine, inspect

    from backend.db import engine as eng
    from backend.db.alembic_config import make_config
    from tests.db.conftest import reversible_tip

    eng.dispose_engine()
    cfg = make_config(db_url)
    command.upgrade(cfg, reversible_tip(db_url))  # head's chain refuses a downgrade past it

    probe = create_engine(db_url)
    try:
        names = inspect(probe).get_table_names()
        # Post-head: revision 0041 has already renamed "uploads" -> "attachments".
        assert "artifacts" in names and "attachments" in names
        assert "email_snapshots" in names and "pending_pushes" in names
        assert "checkpoints" in names
    finally:
        probe.dispose()

    command.downgrade(cfg, "202608091739_configs_cutover")

    probe = create_engine(db_url)
    try:
        names = inspect(probe).get_table_names()
        assert "artifacts" not in names
        assert "pending_pushes" not in names
        assert "checkpoints" not in names
    finally:
        probe.dispose()

    command.upgrade(cfg, "head")  # re-absorbs (still nothing to absorb) — must not raise

    eng.dispose_engine()
    with eng.get_engine().connect() as conn:
        assert mio.export_payload_list(conn, artifacts) == []
        assert mio.export_payload_list(conn, pending_pushes) == []
    eng.dispose_engine()
