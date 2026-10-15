"""Read-only SQL access — the POLICY layer (services/sql_query.py).

The executor's own refusal matrix lives in ``tests/db/test_sql_console.py``. This file proves
the three decisions that layer owns: the kill switch actually kills, the caveats attach to the
statements that earn them (and not to the ones that don't), and every attempt — success or
refusal — leaves an audit row.

The caveat tests are the evidence behind the third claim this feature is allowed to make:
Lucius cannot silently report a wrong money figure.
"""

from __future__ import annotations

import pytest

from backend.core.errors import ValidationError
from backend.db.account_store import AccountStore
from backend.db.sql_query_log_store import SqlQueryLogStore
from backend.services import sql_query


@pytest.fixture
def env(db_isolated):
    AccountStore().create(
        {"id": "acc_a", "name": "Checking", "kind": "asset", "type": "cash", "currency": "SGD"}
    )
    return db_isolated


# ── The kill switch ──────────────────────────────────────────────────────────────────────


def test_it_is_on_by_default(env):
    """Unlike run_code: a read-only, credential-free query defaults ON, or the capability
    silently does nothing for every owner who never finds the toggle."""
    from backend.db.app_config_store import sql_query_enabled

    assert sql_query_enabled() is True


def test_the_kill_switch_blocks_every_entry_point(env):
    from backend.db.app_config_store import set_sql_query_enabled

    set_sql_query_enabled(False)
    for call in (
        lambda: sql_query.run_query("SELECT 1"),
        lambda: sql_query.schema_overview(),
        lambda: sql_query.schema_for_table("accounts"),
    ):
        with pytest.raises(ValidationError) as exc:
            call()
        assert "Settings > Developer" in str(exc.value)


def test_flipping_it_back_on_restores_access(env):
    from backend.db.app_config_store import set_sql_query_enabled

    set_sql_query_enabled(False)
    set_sql_query_enabled(True)
    assert sql_query.run_query("SELECT 1 AS n")["rows"] == [[1]]


# ── Caveats: the money-correctness guardrail ─────────────────────────────────────────────


def test_a_money_column_earns_the_historical_rate_lock_caveat():
    caveats = sql_query.caveats_for("SELECT SUM(base_amount) FROM transactions")
    assert any("base_currency" in c for c in caveats)
    assert any("Do NOT report a figure from this tool" in c for c in caveats)


def test_every_money_column_is_covered():
    for column in sql_query.MONEY_COLUMNS:
        caveats = sql_query.caveats_for(f"SELECT {column} FROM transactions")
        assert any("base_currency" in c for c in caveats), column


def test_touching_transactions_at_all_earns_the_soft_delete_caveat():
    caveats = sql_query.caveats_for("SELECT COUNT(*) FROM transactions")
    assert any("deleted = 1" in c for c in caveats)
    assert any("merged_into" in c for c in caveats)


def test_the_money_caveat_comes_first():
    """Ordering is deliberate — the money warning is the one whose absence produces a
    confidently wrong answer."""
    caveats = sql_query.caveats_for("SELECT base_amount FROM transactions")
    assert "MONEY" in caveats[0]


def test_a_query_that_earns_nothing_gets_nothing():
    """Caveats that fire on everything are caveats nobody reads."""
    assert sql_query.caveats_for("SELECT name FROM accounts") == []


def test_a_payload_query_is_told_about_json_extract():
    caveats = sql_query.caveats_for("SELECT payload FROM positions")
    assert any("json_extract" in c for c in caveats)


def test_caveats_ride_on_the_real_result(env):
    result = sql_query.run_query("SELECT COUNT(*) FROM transactions")
    assert any("deleted = 1" in c for c in result["caveats"])


def test_a_clean_read_carries_an_empty_caveat_list(env):
    assert sql_query.run_query("SELECT name FROM accounts")["caveats"] == []


# ── The audit trail ──────────────────────────────────────────────────────────────────────


def test_a_successful_query_is_logged(env):
    sql_query.run_query("SELECT name FROM accounts", origin="web", session_id="s1")
    row = SqlQueryLogStore().recent()[0]
    assert row["sql"] == "SELECT name FROM accounts"
    assert row["ok"] is True
    assert row["row_count"] == 1
    assert row["origin"] == "web"
    assert row["session_id"] == "s1"


def test_a_refused_query_is_logged_too(env):
    """The owner needs to see attempts, not just successes."""
    with pytest.raises(ValidationError):
        sql_query.run_query("DELETE FROM accounts", origin="web")
    row = SqlQueryLogStore().recent()[0]
    assert row["sql"] == "DELETE FROM accounts"
    assert row["ok"] is False
    assert row["error"]


def test_a_secret_table_attempt_is_logged(env):
    with pytest.raises(ValidationError):
        sql_query.run_query("SELECT * FROM secret_config")
    row = SqlQueryLogStore().recent()[0]
    assert row["ok"] is False
    assert "secret_config" in row["sql"]


def test_a_broken_log_never_breaks_a_working_query(env, monkeypatch):
    """The audit trail is for the owner; losing one row beats refusing an allowed query."""

    def _boom(self, data):
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(SqlQueryLogStore, "append", _boom)
    assert sql_query.run_query("SELECT 1 AS n")["rows"] == [[1]]


def test_recent_queries_is_newest_first(env):
    sql_query.run_query("SELECT 1 AS a")
    sql_query.run_query("SELECT 2 AS b")
    assert sql_query.recent_queries()[0]["sql"] == "SELECT 2 AS b"


# ── Schema introspection ─────────────────────────────────────────────────────────────────


def test_schema_overview_lists_readable_tables_and_points_deeper(env):
    overview = sql_query.schema_overview()
    names = {t["table"] for t in overview["tables"]}
    assert "accounts" in names
    assert "secret_config" not in names
    assert "describe_db_schema" in overview["next"]


def test_schema_for_table_surfaces_the_payload_keys(env):
    described = sql_query.schema_for_table("accounts")
    assert "currency" in described["payload_keys"]
