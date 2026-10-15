from sqlalchemy import inspect

from backend.db import tables as t

LOGS_TABLES = [
    "automation_runs",
    "automation_runtime",
    "batch_ledger",
    "mcp_write_log",
    "model_telemetry",
    "pending_analysis",
    "run_history",
    "tool_call_log",
]


def test_all_eight_logs_tables_registered():
    for name in LOGS_TABLES:
        assert name in t.metadata.tables


def test_logs_tables_are_plain_payload_shape_single_pk_no_book_id():
    for name in LOGS_TABLES:
        table = t.metadata.tables[name]
        assert [c.name for c in table.primary_key.columns] == ["id"]  # single-col PK (write_log)
        assert {c.name for c in table.c} == {"seq", "id", "payload"}  # NO book_id
        assert not table.foreign_keys  # no cross-table / book FK anywhere in this wave


def test_logs_schema_creates_on_both_dialects(db_engine):
    insp = inspect(db_engine)  # SQLite here, Postgres in CI
    for name in LOGS_TABLES:
        assert insp.has_table(name)


def test_automation_runs_is_checkpoint_included_its_seven_siblings_are_excluded():
    """The ONE design split this wave hinges on (controller-accepted): automation_runs is
    checkpoint-INCLUDED (already portable/PORTABLE_FILES today, behavior-preserved); its 7
    siblings are runtime/behavioral logs a /rewind must not resurrect or erase, so they join
    ai.checkpoint._EXCLUDED_TABLES exactly like corrections/asset_brief did in prior waves."""
    from backend.ai.checkpoint import _EXCLUDED_TABLES

    assert "automation_runs" not in _EXCLUDED_TABLES
    for name in LOGS_TABLES:
        if name == "automation_runs":
            continue
        assert name in _EXCLUDED_TABLES, f"{name} must be checkpoint-excluded"
