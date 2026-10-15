"""Revision 0063 — remove self-harness: drop harness_suggestions, clear the
harness_state watermark row, delete the seeded "Self-harness routine scan" automation.

Owner decision 2026-08-28 (see docs/launch/BOARD.md): the feature never actually ran —
a 2026-08-02 automations-unification migration silently disconnected the seeded
automation from the detection code it was meant to trigger. This pins the cleanup
migration's data behavior.

Mirrors `tests/db/test_email_tool_permissions_owner_fix_cleanup.py`'s house pattern:
drive alembic to the revision immediately BEFORE this one, seed real rows through the
pre-removal shape, upgrade to head, and read the result back.
"""

from __future__ import annotations

import pytest
import sqlalchemy as sa
from sqlalchemy import insert, inspect, select, table

from backend.db.tables import DEFAULT_BOOK_ID

from .conftest import patch_all_cutover_sources_missing, reversible_tip


@pytest.fixture()
def upgraded_to_0062(db_url, monkeypatch, tmp_path):
    """The chain up to the revision immediately BEFORE this one."""
    if db_url.startswith("postgresql"):
        from sqlalchemy import create_engine, text

        scrub = create_engine(db_url)
        with scrub.begin() as c:
            c.execute(text("DROP SCHEMA public CASCADE"))
            c.execute(text("CREATE SCHEMA public"))
        scrub.dispose()
    monkeypatch.setenv("ONW_DATABASE_URL", db_url)
    patch_all_cutover_sources_missing(monkeypatch, tmp_path)

    from alembic import command

    from backend.db import engine as eng
    from backend.db.alembic_config import make_config

    eng.dispose_engine()
    cfg = make_config(db_url)
    command.upgrade(cfg, "202608271619_rename_automation_approval_to_permission_mode")
    yield eng.get_engine(), cfg
    eng.dispose_engine()


def _upgrade_to_head(cfg):
    from alembic import command

    command.upgrade(cfg, "head")


def _automations_table():
    return table(
        "automations",
        sa.column("book_id", sa.Text),
        sa.column("seq", sa.Integer),
        sa.column("id", sa.Text),
        sa.column("payload", sa.JSON),
    )


def _runtime_state_table():
    return table(
        "runtime_state",
        sa.column("namespace", sa.Text),
        sa.column("payload", sa.JSON),
    )


def _seed_automation(engine, automation_id: str, system_key: str | None) -> None:
    automations = _automations_table()
    payload = {
        "id": automation_id,
        "name": "Self-harness routine scan" if system_key else "Some other automation",
        "trigger": {"kind": "schedule", "type": "weekly", "day": "monday", "time": "09:00"},
        "execution": {"kind": "lucius"},
    }
    if system_key:
        payload["provenance"] = {"source": "system", "system_key": system_key}
    with engine.begin() as conn:
        seq = len(conn.execute(select(automations.c.seq)).fetchall())
        conn.execute(
            insert(automations).values(
                book_id=DEFAULT_BOOK_ID, seq=seq, id=automation_id, payload=payload
            )
        )


def _seed_runtime_state(engine, namespace: str, payload: dict) -> None:
    runtime_state = _runtime_state_table()
    with engine.begin() as conn:
        conn.execute(insert(runtime_state).values(namespace=namespace, payload=payload))


def test_harness_suggestions_table_is_gone_at_head(upgraded_to_0062):
    engine, cfg = upgraded_to_0062
    _upgrade_to_head(cfg)

    names = inspect(engine).get_table_names()
    assert "harness_suggestions" not in names


def test_seeded_harness_scan_automation_is_deleted(upgraded_to_0062):
    engine, cfg = upgraded_to_0062
    _seed_automation(engine, "auto_harness_scan", "harness_scan")
    _seed_automation(engine, "auto_unrelated", None)

    _upgrade_to_head(cfg)

    automations = _automations_table()
    with engine.connect() as conn:
        ids = {r.id for r in conn.execute(select(automations.c.id))}
    assert "auto_harness_scan" not in ids
    assert "auto_unrelated" in ids


def test_no_seeded_harness_scan_automation_is_a_clean_no_op(upgraded_to_0062):
    engine, cfg = upgraded_to_0062
    _seed_automation(engine, "auto_unrelated", None)

    _upgrade_to_head(cfg)  # must not raise

    automations = _automations_table()
    with engine.connect() as conn:
        ids = {r.id for r in conn.execute(select(automations.c.id))}
    assert ids == {"auto_unrelated"}


def test_harness_state_row_is_deleted_other_runtime_state_namespaces_survive(upgraded_to_0062):
    engine, cfg = upgraded_to_0062
    _seed_runtime_state(engine, "harness_state", {"last_suggested_at": "2026-08-20T00:00:00Z"})
    _seed_runtime_state(engine, "app_config", {"instance_name": "Test"})

    _upgrade_to_head(cfg)

    runtime_state = _runtime_state_table()
    with engine.connect() as conn:
        rows = {r.namespace: r.payload for r in conn.execute(select(runtime_state))}
    assert "harness_state" not in rows
    assert rows["app_config"] == {"instance_name": "Test"}


def test_downgrade_recreates_an_empty_harness_suggestions_table(upgraded_to_0062):
    from alembic import command

    engine, cfg = upgraded_to_0062
    # To the reversible tip, not head: a downgrade from head crosses revisions that refuse one.
    command.upgrade(cfg, reversible_tip(str(engine.url.render_as_string(hide_password=False))))
    command.downgrade(cfg, "202608271619_rename_automation_approval_to_permission_mode")

    names = inspect(engine).get_table_names()
    assert "harness_suggestions" in names
    with engine.connect() as conn:
        count = conn.execute(sa.text("SELECT COUNT(*) FROM harness_suggestions")).scalar_one()
    assert count == 0
