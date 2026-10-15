"""Revision 0043 — drop the stale `core`/`email`-owned `tool_permissions` rows for
`send_email`/`search_messages`/`get_messages` left behind by the pre-`connector="email"`-tag
era (commit `81cbd7b8`).

The load-bearing property: a row that WAS written under one of the 6 stale keys before this
migration is GONE afterward, regardless of its level — neither a stale `email`-owned
`always_allow` (written but never enforced pre-fix) nor a stale `core`-owned `blocked`
(enforced but not email-specific) survives, because keeping either would silently change
enforcement the moment the owner-tag fix ships, with no new operator action. Everything else
in the table — an unrelated `core`-owned tool, the reserved `core:*` YOLO row, and a
DIFFERENT connector's own rows — is untouched.

Mirrors `tests/db/test_tool_permissions_migration.py`'s own house pattern: drive alembic to
the revision immediately BEFORE this one, seed real rows through the CURRENT (post-0040)
`tool_permissions` shape, upgrade to head, and read the result back through the store.
"""

from __future__ import annotations

import pytest
import sqlalchemy as sa
from sqlalchemy import insert, select, table

from backend.db.tool_permission_store import ToolPermissionStore

from .conftest import patch_all_cutover_sources_missing


@pytest.fixture()
def upgraded_to_0042(db_url, monkeypatch, tmp_path):
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
    command.upgrade(cfg, "202608161811_receipt_store")
    yield eng.get_engine(), cfg
    eng.dispose_engine()


def _upgrade_to_head(cfg):
    from alembic import command

    command.upgrade(cfg, "head")


def _perms_table():
    return table(
        "tool_permissions",
        sa.column("seq", sa.Integer),
        sa.column("id", sa.Text),
        sa.column("payload", sa.JSON),
    )


def _seed(engine, connector_id: str, tool: str, level: str) -> None:
    """A raw row through the current (post-0040) `tool_permissions` shape."""
    perms = _perms_table()
    key = f"{connector_id}:{tool}"
    with engine.begin() as conn:
        seq = len(conn.execute(select(perms.c.seq)).fetchall())
        conn.execute(
            insert(perms).values(
                seq=seq,
                id=key,
                payload={
                    "id": key,
                    "connector_id": connector_id,
                    "tool": tool,
                    "level": level,
                    "updated_at": "2026-08-16T00:00:00+00:00",
                },
            )
        )


_STALE_TOOLS = ("send_email", "search_messages", "get_messages")


def test_every_stale_core_and_email_row_for_the_three_tools_is_gone(upgraded_to_0042):
    engine, cfg = upgraded_to_0042
    for tool in _STALE_TOOLS:
        _seed(engine, "core", tool, "always_allow")
        _seed(engine, "email", tool, "blocked")

    _upgrade_to_head(cfg)

    store = ToolPermissionStore(engine=engine)
    for tool in _STALE_TOOLS:
        assert store.get_level("core", tool) is None
        assert store.get_level("email", tool) is None


def test_a_stale_email_owned_always_allow_that_was_never_enforced_does_not_activate(
    upgraded_to_0042,
):
    """The exact scenario finding #1 describes: an operator clicked 'Always allow' on
    send_email from Email's own ladder pre-fix (written, but silently ignored at dispatch).
    That row must not survive to activate for real post-fix."""
    engine, cfg = upgraded_to_0042
    _seed(engine, "email", "send_email", "always_allow")

    _upgrade_to_head(cfg)

    store = ToolPermissionStore(engine=engine)
    assert store.get_level("email", "send_email") is None


def test_a_stale_core_owned_blocked_row_does_not_survive_to_degrade_later(upgraded_to_0042):
    """A pre-existing core-owned block for one of the three tools (set for unrelated
    reasons, since core is a shared bucket) must not survive to become the OPERATOR's
    apparent decision about Email specifically — it is dropped, not preserved."""
    engine, cfg = upgraded_to_0042
    _seed(engine, "core", "search_messages", "blocked")

    _upgrade_to_head(cfg)

    store = ToolPermissionStore(engine=engine)
    assert store.get_level("core", "search_messages") is None


def test_unrelated_core_owned_rows_for_other_tool_names_are_untouched(upgraded_to_0042):
    engine, cfg = upgraded_to_0042
    _seed(engine, "core", "add_transactions", "blocked")
    _seed(engine, "core", "*", "always_allow")  # the reserved YOLO row

    _upgrade_to_head(cfg)

    store = ToolPermissionStore(engine=engine)
    assert store.get_level("core", "add_transactions") == "blocked"
    assert store.get_level("core", "*") == "always_allow"


def test_a_different_connectors_rows_for_the_same_tool_names_are_untouched(upgraded_to_0042):
    """An MCP server or another connector could in principle carry a tool sharing one of
    these names — only the exact `core`/`email` keys are stale, nothing else is."""
    engine, cfg = upgraded_to_0042
    _seed(engine, "mcp:some-server", "send_email", "always_allow")

    _upgrade_to_head(cfg)

    store = ToolPermissionStore(engine=engine)
    assert store.get_level("mcp:some-server", "send_email") == "always_allow"


def test_no_stale_rows_at_all_leaves_the_table_untouched(upgraded_to_0042):
    engine, cfg = upgraded_to_0042
    _seed(engine, "plaid", "sync_plaid_now", "always_allow")

    _upgrade_to_head(cfg)

    store = ToolPermissionStore(engine=engine)
    assert store.get_level("plaid", "sync_plaid_now") == "always_allow"
