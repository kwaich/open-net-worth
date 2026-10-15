"""Live database introspection (db/sql_introspect.py) — the read side of ``describe_db_schema``.

Split from ``test_sql_console.py`` to mirror the module split: that file proves a statement
cannot escape the boundary, this one proves the schema map is accurate and complete enough to
write a working query against — which is the whole reason the second tool exists.

The denial tests live HERE rather than there because introspection is the surface that could
most plausibly become a back door around the table denylist: it enumerates, and an enumerator
that forgets to filter leaks names.
"""

from __future__ import annotations

import pytest

from backend.core.errors import ValidationError
from backend.db import sql_console, sql_introspect
from backend.db.account_store import AccountStore


@pytest.fixture
def seeded(db_engine):
    store = AccountStore(engine=db_engine)
    store.create(
        {"id": "acc_a", "name": "Checking", "kind": "asset", "type": "cash", "currency": "SGD"}
    )
    store.create(
        {
            "id": "acc_b",
            "name": "Card",
            "kind": "liability",
            "type": "credit",
            "currency": "SGD",
        }
    )
    return db_engine


def test_every_table_is_classified_allowed_or_denied():
    """A new table cannot be born unclassified — mirrors tests/test_sensitive_paths.py's own
    drift guard. If this fails, decide deliberately whether the new table is readable."""
    from backend.db import tables as t

    known = set(t.metadata.tables)
    unclassified = known - set(sql_introspect.readable_tables()) - sql_console.DENIED_TABLES
    assert not unclassified, f"unclassified tables: {sorted(unclassified)}"


def test_describe_refuses_a_denied_table(seeded):
    with pytest.raises(ValidationError):
        sql_introspect.describe_table("secret_config")


def test_denied_tables_are_not_even_advertised(seeded):
    names = set(sql_introspect.readable_tables())
    assert not (names & sql_console.DENIED_TABLES)


def test_list_tables_returns_names_with_counts(seeded):
    listed = {row["table"]: row["rows"] for row in sql_introspect.list_tables()}
    assert listed["accounts"] == 2
    assert "secret_config" not in listed


def test_describe_table_explains_the_payload_shape(seeded):
    described = sql_introspect.describe_table("accounts")
    names = {c["name"] for c in described["columns"]}
    assert {"id", "name", "kind", "data"} <= names
    payload_col = next(c for c in described["columns"] if c["name"] == "data")
    assert "json_extract" in payload_col["note"]
    # The keys a model would otherwise have to guess at.
    assert "currency" in described["payload_keys"]


def test_describe_table_flags_money_columns_as_exact_text(seeded):
    described = sql_introspect.describe_table("transactions")
    base_amount = next(c for c in described["columns"] if c["name"] == "base_amount")
    assert "TEXT" in base_amount["note"]


def test_describe_table_flags_the_soft_delete_trap(seeded):
    described = sql_introspect.describe_table("transactions")
    deleted = next(c for c in described["columns"] if c["name"] == "deleted")
    assert "soft-deleted" in deleted["note"]


def test_describe_an_unknown_table_says_how_to_list_them(seeded):
    with pytest.raises(ValidationError) as exc:
        sql_introspect.describe_table("no_such_table")
    assert "describe_db_schema" in str(exc.value)
