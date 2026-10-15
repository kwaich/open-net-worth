"""run_sql_query / describe_db_schema — the AI tool surface (ai/tools/sql.py).

The executor's refusal matrix is proven in ``tests/db/test_sql_console.py`` and the policy
layer in ``tests/services/test_sql_query.py``. What this file proves is the part unique to the
tool boundary: a refusal must reach the MODEL as the structured ``{"error": …}`` contract, not
as an exception that aborts the turn, and the schema tool must actually hand back the payload
shape a model needs to write a working query.
"""

from __future__ import annotations

import json

import pytest

from backend.ai.tools import dispatch
from backend.db.account_store import AccountStore


@pytest.fixture
def env(db_isolated):
    AccountStore().create(
        {"id": "acc_a", "name": "Checking", "kind": "asset", "type": "cash", "currency": "SGD"}
    )
    return db_isolated


async def _call(name: str, args: dict) -> dict:
    text, _change = await dispatch(name, args)
    return json.loads(text)


# ── Registration ─────────────────────────────────────────────────────────────────────────


def test_both_tools_are_registered():
    from backend.ai.tools import registered_tool_names

    names = set(registered_tool_names())
    assert {"run_sql_query", "describe_db_schema"} <= names


def test_the_descriptions_point_at_each_other():
    """A model that runs SQL without reading the schema writes queries against columns that
    do not exist — most stores keep their fields in a `payload` JSON blob."""
    from backend.ai.tools import build_tool_schemas

    schemas = {s["name"]: s for s in build_tool_schemas()}
    assert "describe_db_schema" in schemas["run_sql_query"]["description"]


# ── Happy path ───────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_sql_query_returns_rows(env):
    got = await _call("run_sql_query", {"sql": "SELECT name FROM accounts"})
    assert got["columns"] == ["name"]
    assert got["rows"] == [["Checking"]]
    assert got["showing"] == 1
    assert got["truncated"] is False


@pytest.mark.asyncio
async def test_run_sql_query_carries_caveats_on_a_money_read(env):
    got = await _call("run_sql_query", {"sql": "SELECT base_amount FROM transactions"})
    assert any("base_currency" in c for c in got["caveats"])
    assert any("deleted = 1" in c for c in got["caveats"])


@pytest.mark.asyncio
async def test_run_sql_query_honours_an_explicit_limit(env):
    AccountStore().create(
        {"id": "acc_b", "name": "Savings", "kind": "asset", "type": "cash", "currency": "SGD"}
    )
    got = await _call("run_sql_query", {"sql": "SELECT id FROM accounts", "limit": 1})
    assert got["showing"] == 1
    assert got["truncated"] is True
    assert "unknown" in got["total"]


# ── Refusals reach the model as the error contract, not as an exception ──────────────────


@pytest.mark.parametrize(
    "statement",
    [
        "DELETE FROM accounts",
        "DROP TABLE accounts",
        "SELECT * FROM secret_config",
        "SELECT 1; DELETE FROM accounts",
        "",
    ],
)
@pytest.mark.asyncio
async def test_a_refusal_is_a_structured_error_not_a_crash(env, statement):
    got = await _call("run_sql_query", {"sql": statement})
    assert "error" in got
    assert isinstance(got["error"], str) and got["error"]


@pytest.mark.asyncio
async def test_the_refusal_explains_what_to_do_instead(env):
    got = await _call("run_sql_query", {"sql": "UPDATE accounts SET name = 'x'"})
    assert "read-only" in got["error"].lower()


@pytest.mark.asyncio
async def test_the_kill_switch_reaches_the_model_as_an_error(env):
    from backend.db.app_config_store import set_sql_query_enabled

    set_sql_query_enabled(False)
    try:
        got = await _call("run_sql_query", {"sql": "SELECT 1"})
        assert "Settings > Developer" in got["error"]
    finally:
        set_sql_query_enabled(True)


# ── describe_db_schema ───────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_describe_with_no_table_lists_the_readable_ones(env):
    got = await _call("describe_db_schema", {})
    names = {t["table"] for t in got["tables"]}
    assert "accounts" in names
    assert "secret_config" not in names
    assert "connector_secrets" not in names


@pytest.mark.asyncio
async def test_describe_a_table_hands_back_the_payload_keys(env):
    """The whole reason this tool exists: `SELECT currency FROM accounts` is a guess until
    the model knows `currency` lives inside the row blob (`data` on accounts, `payload` elsewhere)."""
    got = await _call("describe_db_schema", {"table": "accounts"})
    assert "currency" in got["payload_keys"]
    payload_col = next(c for c in got["columns"] if c["name"] in {"payload", "data"})
    assert "json_extract" in payload_col["note"]


@pytest.mark.asyncio
async def test_describe_flags_the_money_and_soft_delete_traps(env):
    got = await _call("describe_db_schema", {"table": "transactions"})
    notes = {c["name"]: c.get("note", "") for c in got["columns"]}
    assert "TEXT" in notes["base_amount"]
    assert "soft-deleted" in notes["deleted"]


@pytest.mark.asyncio
async def test_describe_refuses_a_denied_table_as_an_error(env):
    got = await _call("describe_db_schema", {"table": "secret_config"})
    assert "error" in got


@pytest.mark.asyncio
async def test_describe_an_unknown_table_says_how_to_list_them(env):
    got = await _call("describe_db_schema", {"table": "nope"})
    assert "describe_db_schema" in got["error"]
