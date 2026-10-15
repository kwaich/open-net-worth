"""M4 / scan#1 — the Postgres SQL console: gated on a dedicated role, scrub hardened."""

from __future__ import annotations

import os

import pytest

from backend.core.errors import ValidationError
from backend.db import sql_console


@pytest.fixture
def as_postgres(monkeypatch):
    monkeypatch.setattr(sql_console, "dialect", lambda: "postgresql")
    monkeypatch.delenv(sql_console.CONSOLE_URL_ENV, raising=False)
    monkeypatch.setattr(sql_console, "_console_engine", None)


def test_postgres_console_is_refused_without_a_dedicated_role(as_postgres):
    with pytest.raises(ValidationError, match="dedicated read-only database role"):
        sql_console.execute_readonly("SELECT 1")


# ── _scrub: every string form, so a ';' or a keyword inside one is data ──────────────────


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT 'a;b'",
        "SELECT E'it\\'s; DROP TABLE x'",
        "SELECT $$ ; DROP TABLE x $$",
        "SELECT $q$ ; DROP TABLE x $q$",
        "SELECT /* ; /* nested */ DROP */ 1",
    ],
)
def test_scrub_blanks_strings_and_nested_comments(sql):
    scrubbed = sql_console._scrub(sql)
    assert ";" not in scrubbed and "DROP" not in scrubbed
    sql_console.assert_statement_is_read_only(sql)  # one statement, no banned keyword


def test_scrub_keeps_dollar_parameters():
    assert sql_console._scrub("SELECT $1, $2") == "SELECT $1, $2"


def test_escaped_quote_in_e_string_does_not_end_the_string_early():
    # If the scrubber ended the string at \' it would see "; DELETE" as live SQL.
    with pytest.raises(ValidationError):
        sql_console.assert_statement_is_read_only("SELECT E'a\\'' ; DELETE FROM accounts")


# ── The textual scan still names the bypass shapes (the role is the real boundary) ───────


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT query_to_xml('select * from secret_config', true, true, '')",
        "SELECT query_to_xml(E'select * from secret_config', true, true, '')",
        "SELECT query_to_xml($$select * from secret_config$$, true, true, '')",
        'SELECT * FROM U&"\\0073ecret_config"',
        "SELECT * FROM U&'x'",
    ],
)
def test_scan_refuses_postgres_bypass_shapes(sql):
    with pytest.raises(ValidationError):
        sql_console.assert_tables_allowed(sql)


def test_scan_still_allows_ordinary_literals():
    sql_console.assert_tables_allowed("SELECT payee FROM transactions WHERE payee = 'Café'")


@pytest.mark.skipif(
    not (os.environ.get("ONW_TEST_DATABASE_URL") or "").startswith("postgres"),
    reason="needs a Postgres server (ONW_TEST_DATABASE_URL)",
)
def test_postgres_runs_one_prepared_statement_only(db_engine):
    assert sql_console.execute_readonly("SELECT 1 AS one")["rows"] == [[1]]
    with pytest.raises(ValidationError):
        sql_console.execute_readonly("SELECT 1; SELECT 2")
