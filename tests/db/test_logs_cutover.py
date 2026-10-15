"""Logs cutover (plan 4c, task 2): revision 0014 absorbs logs/telemetry JSON
sources into their Tier-2 tables (built by revision 0013), one all-or-nothing unit.
(Originally 9 sources including harness_suggestions, dropped to 8 on 2026-08-28 when
self-harness was removed and its table dropped by revision 0063 — see
docs/launch/BOARD.md; revision 0014 itself was edited post-hoc to stop absorbing it,
same precedent as revision 0016's tool_grants absorb.)

Mirrors ``tests/db/test_investments_cutover.py``'s shape: no cross-file consistency
pre-check (no FKs between logs tables, no book_id anywhere in this wave) and no
embedded-child explosion (every logs table is either a flat payload list or the one
dict-shaped bespoke store). Covers: exact round-trip per store (direct ``logs_io``
calls, fast) AND through the real migration chain (monkeypatched sources +
``command.upgrade``); the dict-shaped ``model_telemetry`` singleton-per-key store;
full absorb + 8 ``json_cutovers`` rows + 8 renames; the up/down/up cycle.

NO money-guard tests this wave (``logs_io.py``'s own module docstring) — every
fixture below is verbatim opaque payload; nothing here feeds net worth.
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import func, select

from backend.db import logs_io as lio
from backend.db.tables import (
    automation_runs,
    automation_runtime,
    batch_ledger,
    json_cutovers,
    mcp_write_log,
    model_telemetry,
    pending_analysis,
    run_history,
    tool_call_log,
)

LOGS_STORE_NAMES = (
    "automation_runs",
    "automation_runtime",
    "batch_ledger",
    "mcp_write_log",
    "model_telemetry",
    "pending_analysis",
    "run_history",
    "tool_call_log",
)


def _set_database_url_for_test(url: str, monkeypatch) -> None:
    """Postgres's ``db_url`` is the ONE shared CI server (unlike SQLite's
    fresh-tmp_path-per-test): scrub the public schema first so an earlier test's
    already-applied migration history doesn't make THIS test's ``command.upgrade(...,
    "head")`` a silent no-op. Mirrors ``test_investments_cutover.py``'s identical
    helper verbatim."""
    if url.startswith("postgresql"):
        from sqlalchemy import create_engine, text

        scrub = create_engine(url)
        with scrub.begin() as c:
            c.execute(text("DROP SCHEMA public CASCADE"))
            c.execute(text("CREATE SCHEMA public"))
        scrub.dispose()
    monkeypatch.setenv("ONW_DATABASE_URL", url)


# ── Fixtures (deliberately small but non-trivial: unicode, nulls, nested dicts —
# the round-trip proof needs a diff to actually be possible) ────────────────────────

AUTOMATION_RUNS = [
    {
        "id": "run_1",
        "automation_id": "auto_1",
        "session_id": "sess_1",
        "status": "completed",
        "trigger": {"kind": "schedule", "cron": "0 9 * * *"},
        "execution": {"mode": "autonomous"},
        "model": "claude-haiku-4-5",
        "effort": "medium",
        "started_at": "2026-08-01T09:00:00Z",
        "finished_at": "2026-08-01T09:00:42Z",
        "summary": "Checked 3 accounts, no anomalies — café ☕ unicode round-trip",
        "error": None,
        "steps": [{"tool": "list_accounts", "ok": True}],
        "proposed_changes": [],
        "pending_tool_ids": [],
        "context": {},
        "files": [],
        "working_dir": None,
        "input_tokens": 512,
        "output_tokens": 128,
        "cache_creation_tokens": 0,
        "cache_read_tokens": 0,
        "cost_usd": 0.0031,
        "stats": {"tool_calls": 1},
    },
    {
        "id": "run_2",
        "automation_id": "auto_2",
        "session_id": None,
        "status": "failed",
        "trigger": {"kind": "manual"},
        "execution": {"mode": "autonomous"},
        "model": "claude-haiku-4-5",
        "effort": "low",
        "started_at": "2026-08-02T00:00:00Z",
        "finished_at": "2026-08-02T00:00:05Z",
        "summary": "",
        "error": "provider timeout",
        "steps": [],
        "proposed_changes": [],
        "pending_tool_ids": [],
        "context": {},
        "files": [],
        "working_dir": None,
        "input_tokens": None,
        "output_tokens": None,
        "cache_creation_tokens": None,
        "cache_read_tokens": None,
        "cost_usd": None,
        "stats": {},
    },
]

AUTOMATION_RUNTIME = [
    {"automation_id": "auto_1", "state": {"queued_at": "2026-08-01T08:59:00Z", "owner": "sched-1"}}
]

BATCH_LEDGER = [
    {
        "handle": "batch-001",
        "task": "intel",
        "transport": "anthropic",
        "source": "intel_scan",
        "model": "claude-haiku-4-5",
        "custom_ids": ["intel-abc123"],
        "submitted_at": "2026-08-01T00:00:00Z",
        "status": "pending",
        "poll_count": 2,
        "last_polled_at": "2026-08-01T00:10:00Z",
        "metadata": {"url_to_date": {"https://x.com": "2026-01-01"}},
    }
]

MCP_WRITE_LOG = [
    {
        "id": "mcpw_1",
        "ts": "2026-08-01T00:00:00Z",
        "surface": "api_token",
        "tool": "create_transaction",
        "domains": ["ledger"],
        "args_summary": "1 field",
        "base_amount": 42.5,
        "checkpoint_id": "ckpt_1",
        "result_ok": True,
    }
]

MODEL_TELEMETRY = {
    "claude-haiku-4-5": {
        "samples": [{"ttft_ms": 210.5, "tok_per_sec": 55.2, "ts": 1735689600.0}],
        "errors": 0,
        "total": 1,
    },
    "claude-sonnet-4-5": {
        "samples": [],
        "errors": 1,
        "total": 1,
    },
}

PENDING_ANALYSIS = [
    {
        "id": "pan_1",
        "subject": "crash",
        "ticker": "AAPL",
        "signal_id": "sig_1",
        "trigger": "crash_detected",
        "triggers": ["crash_detected"],
        "payload": {"note": "price dropped 8%"},
        "status": "pending",
        "attempts": 0,
        "created_at": "2026-08-01T00:00:00Z",
    }
]

RUN_HISTORY = [
    {
        "id": "run_hist_1",
        "kind": "agent",
        "entity_id": "agt_1",
        "entity_name": "Lucius",
        "started_at": "2026-08-01T00:00:00Z",
        "finished_at": "2026-08-01T00:00:10Z",
        "duration_ms": 10000,
        "status": "ok",
        "error": None,
        "summary": "ran fine",
        "trigger": "manual",
        "finding_id": None,
        "steps": [],
        "cost_usd": 0.002,
    }
]

TOOL_CALL_LOG = [
    {
        "id": "tcall_1",
        "ts": "2026-08-01T00:00:00Z",
        "event": "call",
        "tool": "list_accounts",
        "alias_used": None,
        "origin": "web",
        "session_id": "sess_1",
        "turn_index": 0,
        "call_index": 0,
        "mutated": False,
        "domains": [],
        "ok": True,
        "error_class": None,
        "duration_ms": 12.5,
        "result_chars": 128,
        "input_chars": 4,
        "checkpoint_id": None,
        "gate_outcome": None,
        "gate_args_valid": None,
    }
]

# (store name, table, fixture, id_field) — the 7 generic stores.
_GENERIC_SPECS = [
    ("automation_runs", automation_runs, AUTOMATION_RUNS, "id"),
    ("automation_runtime", automation_runtime, AUTOMATION_RUNTIME, "automation_id"),
    ("batch_ledger", batch_ledger, BATCH_LEDGER, "handle"),
    ("mcp_write_log", mcp_write_log, MCP_WRITE_LOG, "id"),
    ("pending_analysis", pending_analysis, PENDING_ANALYSIS, "id"),
    ("run_history", run_history, RUN_HISTORY, "id"),
    ("tool_call_log", tool_call_log, TOOL_CALL_LOG, "id"),
]


def _patch_all_sources_missing(monkeypatch, tmp_path) -> None:
    """Point every cutover source seam — not just this wave's own eight — at a file
    that does not exist, so a test exercising the real migration chain never
    accidentally reads whatever the ambient (session-shared) DATA_DIR happens to hold.
    Delegates to the canonical cross-wave helper (``tests/db/conftest.py``): a bare
    ``command.upgrade(..., "head")`` runs every revision up to head, not just logs'
    own, so a DIFFERENT wave's source (e.g. Wave D's ``news_sources.json``,
    unconditionally reseeded before every test — see the canonical helper's docstring)
    can still get absorbed and collide on a second upgrade in the same test unless
    EVERY source is patched, not just this file's eight."""
    from tests.db.conftest import patch_all_cutover_sources_missing

    patch_all_cutover_sources_missing(monkeypatch, tmp_path)


def _write_all_eight_fixtures(tmp_path, monkeypatch) -> dict[str, tuple]:
    """Write every store's fixture to its own file under ``tmp_path`` and monkeypatch
    the matching ``versions_evidence_source`` constant at it. Returns
    ``{attr: (filename, original)}`` for assertion convenience."""
    files = {
        "AUTOMATION_RUNS_SOURCE": ("automation_runs.json", AUTOMATION_RUNS),
        "AUTOMATION_RUNTIME_SOURCE": ("automation_runtime.json", AUTOMATION_RUNTIME),
        "BATCH_LEDGER_SOURCE": ("batch_ledger.json", BATCH_LEDGER),
        "MCP_WRITE_LOG_SOURCE": ("mcp_write_log.json", MCP_WRITE_LOG),
        "MODEL_TELEMETRY_SOURCE": ("model_telemetry.json", MODEL_TELEMETRY),
        "PENDING_ANALYSIS_SOURCE": ("pending_analysis.json", PENDING_ANALYSIS),
        "RUN_HISTORY_SOURCE": ("run_history.json", RUN_HISTORY),
        "TOOL_CALL_LOG_SOURCE": ("tool_call_log.json", TOOL_CALL_LOG),
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
def test_each_generic_store_round_trips_exactly(db_engine, store, table, fixture, id_field):
    with db_engine.begin() as conn:
        n = lio.import_payload_list(conn, table, fixture, id_field=id_field)
    assert n == len(fixture)
    with db_engine.connect() as conn:
        assert lio.export_payload_list(conn, table) == fixture


def test_generic_import_has_no_book_id_column_at_all(db_engine):
    with db_engine.begin() as conn:
        lio.import_payload_list(conn, run_history, RUN_HISTORY)
    with db_engine.connect() as conn:
        row = conn.execute(select(run_history).where(run_history.c.id == "run_hist_1")).one()
    assert not hasattr(row, "book_id")
    assert row.seq == 0
    assert "book_id" not in row.payload


def test_automation_runtime_keyed_by_automation_id_id_field_key_stays_in_payload(db_engine):
    with db_engine.begin() as conn:
        lio.import_payload_list(
            conn, automation_runtime, AUTOMATION_RUNTIME, id_field="automation_id"
        )
    with db_engine.connect() as conn:
        row = conn.execute(
            select(automation_runtime).where(automation_runtime.c.id == "auto_1")
        ).one()
    assert row.payload["automation_id"] == "auto_1"


def test_batch_ledger_keyed_by_handle_id_field_key_stays_in_payload(db_engine):
    with db_engine.begin() as conn:
        lio.import_payload_list(conn, batch_ledger, BATCH_LEDGER, id_field="handle")
    with db_engine.connect() as conn:
        row = conn.execute(select(batch_ledger).where(batch_ledger.c.id == "batch-001")).one()
    assert row.payload["handle"] == "batch-001"


def test_missing_source_files_are_a_clean_empty_export_for_every_generic_store(db_engine):
    with db_engine.connect() as conn:
        for _store, table, _fixture, _id_field in _GENERIC_SPECS:
            assert lio.export_payload_list(conn, table) == []


# ── model_telemetry: dict-shaped, one row per model, payload has no "model" key ─────


def test_model_telemetry_round_trips_the_dict_verbatim(db_engine):
    with db_engine.begin() as conn:
        n = lio.import_model_telemetry(conn, MODEL_TELEMETRY)
    assert n == 2
    with db_engine.connect() as conn:
        assert lio.export_model_telemetry(conn) == MODEL_TELEMETRY


def test_model_telemetry_default_when_no_rows_exist(db_engine):
    with db_engine.connect() as conn:
        assert lio.export_model_telemetry(conn) == {}


def test_model_telemetry_keyed_by_model_id_payload_has_no_model_key_inside(db_engine):
    with db_engine.begin() as conn:
        lio.import_model_telemetry(conn, MODEL_TELEMETRY)
    with db_engine.connect() as conn:
        row = conn.execute(
            select(model_telemetry).where(model_telemetry.c.id == "claude-haiku-4-5")
        ).one()
    assert row.payload == MODEL_TELEMETRY["claude-haiku-4-5"]
    assert "model" not in row.payload
    assert not hasattr(row, "book_id")


def test_model_telemetry_empty_dict_imports_zero_rows(db_engine):
    with db_engine.begin() as conn:
        n = lio.import_model_telemetry(conn, {})
    assert n == 0
    with db_engine.connect() as conn:
        assert lio.export_model_telemetry(conn) == {}


# ── LOGS_PAYLOAD_SPECS / LOGS_FILES constants ────────────────────────────────────


def test_logs_payload_specs_covers_the_seven_generic_stores():
    specs = {fn: (table, id_field) for fn, table, id_field in lio.LOGS_PAYLOAD_SPECS}
    assert set(specs) == {
        "automation_runs.json",
        "automation_runtime.json",
        "batch_ledger.json",
        "mcp_write_log.json",
        "pending_analysis.json",
        "run_history.json",
        "tool_call_log.json",
    }
    assert specs["automation_runtime.json"] == (automation_runtime, "automation_id")
    assert specs["batch_ledger.json"] == (batch_ledger, "handle")
    for fn, (_table, id_field) in specs.items():
        if fn not in ("automation_runtime.json", "batch_ledger.json"):
            assert id_field == "id"


def test_logs_files_lists_all_eight():
    assert set(lio.LOGS_FILES) == {
        "automation_runs.json",
        "automation_runtime.json",
        "batch_ledger.json",
        "mcp_write_log.json",
        "model_telemetry.json",
        "pending_analysis.json",
        "run_history.json",
        "tool_call_log.json",
    }
    assert len(lio.LOGS_FILES) == 8


# ── Full migration chain: monkeypatched sources + command.upgrade(head) ─────────────

# These tests prove what the cutover absorbs, so they stop at it: revision 202609281300 (data
# hygiene) deletes run history of the retired agent product and automation runs whose automation
# is gone, which is exactly what the fixtures below are.
_LOGS_CUTOVER = "202608090747_logs_cutover"


def test_all_eight_stores_round_trip_exactly_through_the_real_migration(
    tmp_path, db_url, monkeypatch
):
    files = _write_all_eight_fixtures(tmp_path, monkeypatch)
    _set_database_url_for_test(db_url, monkeypatch)

    from alembic import command

    from backend.db import engine as eng
    from backend.db.alembic_config import make_config

    eng.dispose_engine()
    command.upgrade(make_config(db_url), _LOGS_CUTOVER)

    exporters = {
        "AUTOMATION_RUNS_SOURCE": lambda c: lio.export_payload_list(c, automation_runs),
        "AUTOMATION_RUNTIME_SOURCE": lambda c: lio.export_payload_list(c, automation_runtime),
        "BATCH_LEDGER_SOURCE": lambda c: lio.export_payload_list(c, batch_ledger),
        "MCP_WRITE_LOG_SOURCE": lambda c: lio.export_payload_list(c, mcp_write_log),
        "MODEL_TELEMETRY_SOURCE": lambda c: lio.export_model_telemetry(c),
        "PENDING_ANALYSIS_SOURCE": lambda c: lio.export_payload_list(c, pending_analysis),
        "RUN_HISTORY_SOURCE": lambda c: lio.export_payload_list(c, run_history),
        "TOOL_CALL_LOG_SOURCE": lambda c: lio.export_payload_list(c, tool_call_log),
    }
    with eng.get_engine().connect() as conn:
        for attr, (_filename, original) in files.items():
            assert exporters[attr](conn) == original, f"{attr} did not round-trip exactly"

    eng.dispose_engine()


def test_one_store_absent_is_a_clean_skip_other_seven_still_absorb(tmp_path, db_url, monkeypatch):
    """Any one store may be absent (e.g. a fresh install that never wrote
    mcp_write_log.json) — absorbed only if present; the other seven still absorb
    normally when only this one is missing. Mirrors
    test_asset_brief_absent_is_a_clean_skip_other_seven_still_absorb."""
    _write_all_eight_fixtures(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "backend.db.alembic.versions_evidence_source.MCP_WRITE_LOG_SOURCE",
        tmp_path / "does_not_exist_mcp_write_log.json",
    )
    _set_database_url_for_test(db_url, monkeypatch)

    from alembic import command

    from backend.db import engine as eng
    from backend.db.alembic_config import make_config

    eng.dispose_engine()
    command.upgrade(make_config(db_url), _LOGS_CUTOVER)

    with eng.get_engine().connect() as conn:
        assert lio.export_payload_list(conn, mcp_write_log) == []
        assert lio.export_payload_list(conn, automation_runs) == AUTOMATION_RUNS
        assert lio.export_model_telemetry(conn) == MODEL_TELEMETRY
        count = conn.execute(
            select(func.count())
            .select_from(json_cutovers)
            .where(json_cutovers.c.store == "mcp_write_log")
        ).scalar_one()
    assert count == 0
    eng.dispose_engine()


def test_full_migration_is_a_clean_empty_absorb_when_all_eight_sources_missing(
    tmp_path, db_url, monkeypatch
):
    """Fresh install: none of the eight logs files exist — head is reached, zero rows,
    zero cutover-ledger entries for the logs stores."""
    _patch_all_sources_missing(monkeypatch, tmp_path)
    monkeypatch.setattr("backend.db.migrate.BACKUP_DIR", tmp_path / "backups" / "db")
    _set_database_url_for_test(db_url, monkeypatch)

    from backend.db import engine as eng

    eng.dispose_engine()
    from backend.db.migrate import ensure_migrated

    ensure_migrated()

    with eng.get_engine().connect() as conn:
        for _store, table, _fixture, _id_field in _GENERIC_SPECS:
            assert lio.export_payload_list(conn, table) == []
        assert lio.export_model_telemetry(conn) == {}

        count = conn.execute(
            select(func.count())
            .select_from(json_cutovers)
            .where(json_cutovers.c.store.in_(LOGS_STORE_NAMES))
        ).scalar_one()
    assert count == 0
    eng.dispose_engine()


@pytest.mark.parametrize(
    "attr,filename,bad_payload",
    [
        ("AUTOMATION_RUNS_SOURCE", "automation_runs.json", {"not": "a list"}),  # dict, not list
        ("MODEL_TELEMETRY_SOURCE", "model_telemetry.json", ["not", "a", "dict"]),  # list, not dict
    ],
    ids=["automation_runs-dict-not-list", "model_telemetry-list-not-dict"],
)
def test_wrong_top_level_shape_aborts_the_whole_migration(
    tmp_path, db_url, monkeypatch, attr, filename, bad_payload
):
    _patch_all_sources_missing(monkeypatch, tmp_path)
    path = tmp_path / filename
    path.write_text(json.dumps(bad_payload))
    monkeypatch.setattr(f"backend.db.alembic.versions_evidence_source.{attr}", path)
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
    successful import — proven on ``run_history`` by monkeypatching the SHARED generic
    ``export_payload_list`` (every generic store routes through it, so this proves the
    guard fires regardless of which specific store drifts): the WHOLE migration must
    still RAISE (fail-closed, all-or-nothing), not silently accept the drifted store."""
    _patch_all_sources_missing(monkeypatch, tmp_path)
    original = list(RUN_HISTORY)
    path = tmp_path / "run_history.json"
    path.write_text(json.dumps(original, ensure_ascii=False))
    monkeypatch.setattr("backend.db.alembic.versions_evidence_source.RUN_HISTORY_SOURCE", path)
    monkeypatch.setattr(
        lio, "export_payload_list", lambda conn, table, reverse=False: [{"drifted": True}]
    )
    _set_database_url_for_test(db_url, monkeypatch)

    from alembic import command

    from backend.db import engine as eng
    from backend.db.alembic_config import make_config

    eng.dispose_engine()
    with pytest.raises(RuntimeError, match="round-trip verification FAILED") as exc_info:
        command.upgrade(make_config(db_url), "head")
    assert "run_history" in str(exc_info.value)
    eng.dispose_engine()


def test_full_migration_absorbs_all_eight_and_renames(tmp_path, db_url, monkeypatch):
    files = _write_all_eight_fixtures(tmp_path, monkeypatch)
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
    for store in LOGS_STORE_NAMES:
        assert rows[store].renamed == 1
        assert rows[store].source_hash

    eng.dispose_engine()


# ── Up/down/up cycle ──────────────────────────────────────────────────────────────────


def test_up_down_up_cycle_is_reversible(db_url, tmp_path, monkeypatch):
    """Structural reversibility of the SCHEMA CHAIN, isolated from data-absorption
    correctness (already fully proven by the tests above): every one of the nine
    sources is explicitly pointed at a nonexistent file, so both upgrade passes are
    clean empty absorbs — the down step (to 0012, dropping the 9 logs tables via
    0013's own downgrade; revision 0014's OWN downgrade() is a documented no-op — see
    that revision) has nothing to lose, and re-upgrading cannot collide on a stale
    ``json_cutovers`` primary key from a first pass that inserted zero rows. Uses its
    own throwaway ``create_engine`` probes for inspection (never the app singleton)
    around the downgrade step, so there is no question of a stale cached engine."""
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
        assert "automation_runs" in names and "model_telemetry" in names
        assert "tool_call_log" in names
    finally:
        probe.dispose()

    command.downgrade(cfg, "202608090350_investments_cutover")

    probe = create_engine(db_url)
    try:
        names = inspect(probe).get_table_names()
        assert "automation_runs" not in names
        assert "model_telemetry" not in names
        assert "tool_call_log" not in names
    finally:
        probe.dispose()

    command.upgrade(cfg, "head")  # re-absorbs (still nothing to absorb) — must not raise

    eng.dispose_engine()
    with eng.get_engine().connect() as conn:
        assert lio.export_payload_list(conn, automation_runs) == []
        assert lio.export_model_telemetry(conn) == {}
    eng.dispose_engine()


# ── Regression: the 3 PREPEND-order stores preserve chronological order across the
# migration boundary (wave review Blocker — data corruption, empirically reproduced
# against the live tree). Every fixture above this point happens to be single-row for
# mcp_write_log/run_history/tool_call_log, which cannot distinguish
# import direction (a 1-element list round-trips identically either way) — these tests
# use a real MULTI-row, PREPEND-order (newest-first) fixture, exactly the on-disk shape
# ``items.insert(0, new)`` produces, and run the REAL migration chain via
# ``command.upgrade(..., "head")`` per the review's own prescription. ─────────────────


def test_tool_call_log_prepend_order_survives_the_migration_boundary(tmp_path, db_url, monkeypatch):
    """RED-PROOF (plan 4c wave review Blocker): the source file on disk is
    PREPEND-order — newest-first, exactly as the real (pre-cutover) ``ToolCallLogStore
    .append_many`` wrote it via ``items.insert(0, ...)``. Against the PRE-FIX cutover
    (plain ``enumerate(raw)``), the newest pre-existing row (``raw[0]``) would land at
    seq=0; ``ToolCallLogStore``'s own ``ORDER BY seq DESC`` read then returns the
    OLDEST row first for every pre-existing row — chronological order INVERTED. (Task
    report has the transcript of this test run red against the pre-fix code.)

    Proves both halves the review asked for:
      (a) the store's newest-first read returns TRUE chronological order for the
          migrated (pre-existing) rows — this is what actually fails pre-fix;
      (b) a row appended AFTER the migration continues the seq sequence as the newest
          entry — seq continuity across the migration boundary (holds either way, since
          ``_next_seq`` always takes the table's current max — recorded here as the
          review's requested proof, not as evidence of the bug itself).
    Also re-proves round-trip export==source byte-exact for this multi-row case
    specifically (the existing single-row fixtures in this file can't tell direction
    apart at all)."""
    fixture = [  # on-disk order: newest-first, exactly like items.insert(0, new) writes it
        {**TOOL_CALL_LOG[0], "id": "tcall_3", "ts": "2026-08-03T00:00:00Z"},
        {**TOOL_CALL_LOG[0], "id": "tcall_2", "ts": "2026-08-02T00:00:00Z"},
        {**TOOL_CALL_LOG[0], "id": "tcall_1", "ts": "2026-08-01T00:00:00Z"},
    ]
    _patch_all_sources_missing(monkeypatch, tmp_path)
    path = tmp_path / "tool_call_log.json"
    path.write_text(json.dumps(fixture, ensure_ascii=False))
    monkeypatch.setattr("backend.db.alembic.versions_evidence_source.TOOL_CALL_LOG_SOURCE", path)
    _set_database_url_for_test(db_url, monkeypatch)

    from alembic import command

    from backend.db import engine as eng
    from backend.db.alembic_config import make_config

    eng.dispose_engine()
    command.upgrade(make_config(db_url), "head")

    from backend.db.tool_call_log_store import ToolCallLogStore

    store = ToolCallLogStore()

    # (a) migrated rows read back in TRUE chronological newest-first order. THIS is the
    # assertion that fails pre-fix (RED) — everything below it is supplementary.
    ids = [r["id"] for r in store.all_rows()]
    assert ids == ["tcall_3", "tcall_2", "tcall_1"], (
        f"chronological order inverted for pre-existing rows: expected newest-first "
        f"[tcall_3, tcall_2, tcall_1], got {ids}"
    )

    # Bonus: round-trip is still byte-exact for the multi-row prepend case specifically
    # (the existing single-row fixtures elsewhere in this file can't tell direction apart).
    # Must run BEFORE (b) appends a 4th row, since `fixture` only has the original 3.
    with eng.get_engine().connect() as conn:
        assert lio.export_payload_list(conn, tool_call_log, reverse=True) == fixture

    # (b) a row appended AFTER the migration continues the seq sequence as newest.
    store.append({**TOOL_CALL_LOG[0], "id": "tcall_4", "ts": "2026-08-04T00:00:00Z"})
    ids_after_append = [r["id"] for r in store.all_rows()]
    assert ids_after_append == ["tcall_4", "tcall_3", "tcall_2", "tcall_1"], (
        f"seq continuity broken across the migration boundary: expected the newly "
        f"appended row first, got {ids_after_append}"
    )

    eng.dispose_engine()


def test_run_history_prepend_order_survives_the_migration_boundary(tmp_path, db_url, monkeypatch):
    """Same RED-PROOF shape as ``test_tool_call_log_prepend_order_survives_the_migration_
    boundary`` (see its docstring for the full mechanics) — the other store the review's
    own live-impact estimate named explicitly (120 rows on the live tree)."""
    fixture = [  # on-disk order: newest-first
        {**RUN_HISTORY[0], "id": "run_hist_3", "started_at": "2026-08-03T00:00:00Z"},
        {**RUN_HISTORY[0], "id": "run_hist_2", "started_at": "2026-08-02T00:00:00Z"},
        {**RUN_HISTORY[0], "id": "run_hist_1", "started_at": "2026-08-01T00:00:00Z"},
    ]
    _patch_all_sources_missing(monkeypatch, tmp_path)
    path = tmp_path / "run_history.json"
    path.write_text(json.dumps(fixture, ensure_ascii=False))
    monkeypatch.setattr("backend.db.alembic.versions_evidence_source.RUN_HISTORY_SOURCE", path)
    _set_database_url_for_test(db_url, monkeypatch)

    from alembic import command

    from backend.db import engine as eng
    from backend.db.alembic_config import make_config

    eng.dispose_engine()
    command.upgrade(make_config(db_url), _LOGS_CUTOVER)

    from backend.db.run_history_store import RunHistoryStore

    store = RunHistoryStore()

    # (a) migrated rows read back in TRUE chronological newest-first order. THIS is the
    # assertion that fails pre-fix (RED) — everything below it is supplementary.
    ids = [r["id"] for r in store.for_entity("agent", "agt_1", limit=50)]
    assert ids == ["run_hist_3", "run_hist_2", "run_hist_1"], (
        f"chronological order inverted for pre-existing rows: expected newest-first "
        f"[run_hist_3, run_hist_2, run_hist_1], got {ids}"
    )

    # Bonus: round-trip is still byte-exact for the multi-row prepend case specifically
    # (the existing single-row fixtures elsewhere in this file can't tell direction apart).
    # Must run BEFORE (b) appends a 4th row, since `fixture` only has the original 3.
    with eng.get_engine().connect() as conn:
        assert lio.export_payload_list(conn, run_history, reverse=True) == fixture

    # (b) a row appended AFTER the migration continues the seq sequence as newest.
    store.append({**RUN_HISTORY[0], "id": "run_hist_4", "started_at": "2026-08-04T00:00:00Z"})
    ids_after_append = [r["id"] for r in store.for_entity("agent", "agt_1", limit=50)]
    assert ids_after_append == ["run_hist_4", "run_hist_3", "run_hist_2", "run_hist_1"], (
        f"seq continuity broken across the migration boundary: expected the newly "
        f"appended row first, got {ids_after_append}"
    )

    eng.dispose_engine()
