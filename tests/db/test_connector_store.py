"""ConnectorStore — rows, the one secrets rule, redacted state, cascade and composition."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from backend.core.errors import ConflictError, NotFoundError
from backend.db.connector_store import ConnectorStore
from backend.db.engine import get_engine, locked_unit_of_work
from backend.db.tables import connector_secrets, connectors


@pytest.fixture
def store(db_engine):
    """A store over an empty ``connectors`` table (the migration seeds the two price sources)."""
    with db_engine.begin() as conn:
        conn.execute(connectors.delete())
    return ConnectorStore()


def _make(store: ConnectorStore, cid: str = "plaid", **kw) -> dict:
    return store.create(cid, kw.pop("kind", cid), kw.pop("name", "Plaid"), **kw)


# ── rows ─────────────────────────────────────────────────────────────────────────────────


def test_create_and_get_round_trip(store):
    row = _make(store, config={"environment": "sandbox"}, state={"last_status": "ok"}, enabled=True)

    assert store.get("plaid") == row
    assert row["enabled"] is True
    assert row["config"] == {"environment": "sandbox"}
    assert row["state"] == {"last_status": "ok"}
    assert row["created_at"] == row["updated_at"]


def test_get_unknown_connector_is_none(store):
    assert store.get("nope") is None


def test_create_defaults_are_disabled_and_empty(store):
    row = _make(store)
    assert (row["enabled"], row["config"], row["state"]) == (False, {}, {})


def test_create_duplicate_id_conflicts(store):
    _make(store)
    with pytest.raises(ConflictError):
        _make(store)


def test_ensure_creates_once_and_returns_the_existing_row(store):
    first = store.ensure("email", "email", "Email")
    store.patch_config("email", {"address": "me@example.com"})

    again = store.ensure("email", "email", "Email")

    assert first["config"] == {}
    assert again["config"] == {"address": "me@example.com"}
    assert len(store.list()) == 1


def test_ensure_refuses_a_row_of_another_kind(store):
    store.ensure("email", "email", "Email")
    with pytest.raises(ConflictError):
        store.ensure("email", "webhook", "Webhook")


def test_list_filters_by_kind_and_orders_by_creation(store):
    store.create("web_search:tavily", "web_search", "Tavily")
    store.create("plaid", "plaid", "Plaid")
    store.create("web_search:serper", "web_search", "Serper")

    assert {r["id"] for r in store.list()} == {"web_search:tavily", "plaid", "web_search:serper"}
    assert {r["id"] for r in store.list("web_search")} == {"web_search:tavily", "web_search:serper"}
    assert store.list("nothing") == []


def test_list_rows_never_carry_secrets(store):
    _make(store)
    store.set_secrets("plaid", {"secret": "s3cret"})
    assert all("secrets" not in r and "s3cret" not in str(r) for r in store.list())


def test_enabled_flags_is_the_lean_listing(store):
    _make(store, "plaid", enabled=True, config={"environment": "sandbox"})
    _make(store, "ynab", name="YNAB", state={"last_status": "ok"})

    assert store.enabled_flags() == [
        {"id": "plaid", "kind": "plaid", "enabled": True},
        {"id": "ynab", "kind": "ynab", "enabled": False},
    ]


def test_secrets_by_kind_reads_every_row_of_a_kind_in_one_query(store, db_engine):
    _make(store, "mcp:a", kind="mcp", name="A")
    _make(store, "mcp:b", kind="mcp", name="B")
    _make(store, "mcp:none", kind="mcp", name="No secrets")
    _make(store, "plaid")
    store.set_secrets("mcp:a", {"url": "https://a.example/mcp"})
    store.set_secrets("mcp:b", {"url": "https://b.example/mcp", "token": "t"})
    store.set_secrets("plaid", {"secret": "other-kind"})

    statements: list[str] = []
    from sqlalchemy import event

    def record(conn, cursor, statement, *rest):
        statements.append(statement)

    event.listen(db_engine, "before_cursor_execute", record)
    try:
        found = store.secrets_by_kind("mcp")
    finally:
        event.remove(db_engine, "before_cursor_execute", record)

    assert found == {
        "mcp:a": {"url": "https://a.example/mcp"},
        "mcp:b": {"url": "https://b.example/mcp", "token": "t"},
    }
    assert len(statements) == 1


def test_patch_config_merges_and_none_removes_a_key(store):
    _make(store, config={"client_id": "abc", "environment": "sandbox"})

    row = store.patch_config("plaid", {"environment": "production", "client_id": None, "x": 1})

    assert row["config"] == {"environment": "production", "x": 1}
    assert store.get("plaid")["config"] == {"environment": "production", "x": 1}


def test_replace_config_swaps_the_whole_dict(store):
    _make(store, config={"a": 1, "b": 2})
    store.replace_config("plaid", {"c": 3})
    assert store.get("plaid")["config"] == {"c": 3}


def test_set_enabled_and_set_name(store):
    _make(store)
    store.set_enabled("plaid", True)
    store.set_name("plaid", "Plaid (work)")

    row = store.get("plaid")
    assert row["enabled"] is True and row["name"] == "Plaid (work)"
    assert row["updated_at"] >= row["created_at"]


@pytest.mark.parametrize(
    "call",
    [
        lambda s: s.patch_config("ghost", {"a": 1}),
        lambda s: s.replace_config("ghost", {}),
        lambda s: s.set_enabled("ghost", True),
        lambda s: s.set_name("ghost", "x"),
        lambda s: s.merge_state("ghost", {"a": 1}),
        lambda s: s.set_secrets("ghost", {"a": "b"}),
        lambda s: s.replace_secrets("ghost", {"a": "b"}),
    ],
)
def test_mutating_an_unknown_connector_raises_not_found(store, call):
    with pytest.raises(NotFoundError):
        call(store)


# ── state ────────────────────────────────────────────────────────────────────────────────


def test_merge_state_merges_and_none_removes_a_key(store):
    _make(store, state={"last_status": "ok", "cursor": "7"})

    row = store.merge_state("plaid", {"cursor": None, "last_synced_at": "2026-10-10T00:00:00Z"})

    assert row["state"] == {"last_status": "ok", "last_synced_at": "2026-10-10T00:00:00Z"}


@pytest.mark.parametrize("key", ["last_error", "error"])
def test_merge_state_redacts_credentials_in_error_strings(store, key):
    _make(store, "simplefin")

    store.merge_state(
        "simplefin",
        {key: "GET https://u1:pw9@bridge.example/simplefin?api_key=K123&limit=3 -> 403"},
    )

    stored = store.get("simplefin")["state"][key]
    assert "pw9" not in stored and "K123" not in stored
    assert stored == "GET https://***@bridge.example/simplefin?api_key=***&limit=3 -> 403"


def test_merge_state_leaves_other_values_and_non_string_errors_alone(store):
    _make(store)

    store.merge_state(
        "plaid", {"note": "token=keepme", "last_error": None, "accounts_meta": {"a": 1}}
    )

    state = store.get("plaid")["state"]
    assert state == {"note": "token=keepme", "accounts_meta": {"a": 1}}


# ── secrets ──────────────────────────────────────────────────────────────────────────────


def test_secrets_default_to_empty(store):
    _make(store)
    assert store.secrets("plaid") == {}
    assert store.secret_fields("plaid") == set()


def test_set_secrets_sets_and_reads_back(store):
    _make(store)

    fields = store.set_secrets("plaid", {"secret": "s1", "access_token": "t1"})

    assert fields == {"secret", "access_token"}
    assert store.secrets("plaid") == {"secret": "s1", "access_token": "t1"}
    assert store.secret_fields("plaid") == {"secret", "access_token"}


@pytest.mark.parametrize("blank", ["", "   ", "\n\t"])
def test_a_blank_secret_keeps_the_saved_one(store, blank):
    _make(store)
    store.set_secrets("plaid", {"secret": "s1"})

    store.set_secrets("plaid", {"secret": blank})

    assert store.secrets("plaid") == {"secret": "s1"}


def test_a_blank_secret_with_nothing_saved_stores_nothing(store):
    _make(store)
    store.set_secrets("plaid", {"secret": "  "})
    assert store.secrets("plaid") == {}
    assert store.secret_fields("plaid") == set()


def test_none_clears_only_that_field(store):
    _make(store)
    store.set_secrets("plaid", {"secret": "s1", "access_token": "t1"})

    store.set_secrets("plaid", {"access_token": None})

    assert store.secrets("plaid") == {"secret": "s1"}


def test_clearing_the_last_secret_removes_the_row(store):
    _make(store)
    store.set_secrets("plaid", {"secret": "s1"})
    store.set_secrets("plaid", {"secret": None})

    with get_engine().connect() as conn:
        assert conn.execute(select(connector_secrets)).all() == []


def test_structured_secrets_are_stored_and_an_empty_one_removes_the_field(store):
    store.create("mcp:db", "mcp", "DB")
    store.set_secrets("mcp:db", {"args": ["--dsn", "postgresql://u:pw@h/db"], "env": {"A": "1"}})
    assert store.secret_fields("mcp:db") == {"args", "env"}

    store.set_secrets("mcp:db", {"args": [], "env": {"A": "2"}})

    assert store.secrets("mcp:db") == {"env": {"A": "2"}}


def test_a_secret_value_is_stored_verbatim(store):
    _make(store)
    pem = "-----BEGIN KEY-----\nabc\n-----END KEY-----\n"
    store.set_secrets("plaid", {"secret": pem})
    assert store.secrets("plaid")["secret"] == pem


def test_replace_secrets_swaps_the_set_and_drops_blanks(store):
    _make(store)
    store.set_secrets("plaid", {"secret": "old", "access_token": "old"})

    fields = store.replace_secrets("plaid", {"secret": "new", "access_token": "", "item_id": None})

    assert fields == {"secret"}
    assert store.secrets("plaid") == {"secret": "new"}


def test_replace_secrets_with_nothing_clears_them(store):
    _make(store)
    store.set_secrets("plaid", {"secret": "s"})
    store.replace_secrets("plaid", {})
    assert store.secrets("plaid") == {}


def test_secrets_are_per_connector(store):
    _make(store, "plaid")
    _make(store, "ynab", name="YNAB")
    store.set_secrets("plaid", {"secret": "p"})
    store.set_secrets("ynab", {"access_token": "y"})

    assert store.secrets("plaid") == {"secret": "p"}
    assert store.secrets("ynab") == {"access_token": "y"}


# ── delete / cascade ─────────────────────────────────────────────────────────────────────


def test_delete_removes_the_row_and_its_secrets(store):
    _make(store)
    store.set_secrets("plaid", {"secret": "s"})

    assert store.delete("plaid") is True

    assert store.get("plaid") is None
    with get_engine().connect() as conn:
        assert conn.execute(select(connector_secrets)).all() == []


def test_delete_unknown_connector_is_false(store):
    assert store.delete("ghost") is False


def test_the_foreign_key_cascades_a_raw_delete_of_the_connector(store):
    _make(store)
    store.set_secrets("plaid", {"secret": "s"})

    with get_engine().begin() as conn:
        conn.execute(connectors.delete().where(connectors.c.id == "plaid"))

    with get_engine().connect() as conn:
        assert conn.execute(select(connector_secrets)).all() == []


# ── composition ──────────────────────────────────────────────────────────────────────────


def test_conn_composes_several_mutations_into_one_transaction(store):
    with locked_unit_of_work(connectors, connector_secrets) as conn:
        store.create("lunch_flow", "lunch_flow", "Lunch Flow", conn=conn)
        store.set_secrets("lunch_flow", {"access_token": "a", "refresh_token": "r"}, conn=conn)
        store.merge_state("lunch_flow", {"token_expires_at": 1.5}, conn=conn)
        # visible to the same transaction before it commits
        assert store.secrets("lunch_flow", conn=conn) == {"access_token": "a", "refresh_token": "r"}

    assert store.get("lunch_flow")["state"] == {"token_expires_at": 1.5}
    assert store.secret_fields("lunch_flow") == {"access_token", "refresh_token"}


def test_a_failure_inside_a_composed_unit_rolls_everything_back(store):
    with pytest.raises(RuntimeError):
        with locked_unit_of_work(connectors, connector_secrets) as conn:
            store.create("lunch_flow", "lunch_flow", "Lunch Flow", conn=conn)
            store.set_secrets("lunch_flow", {"access_token": "a"}, conn=conn)
            raise RuntimeError("boom")

    assert store.get("lunch_flow") is None
    assert store.secrets("lunch_flow") == {}


def test_a_failed_standalone_mutation_leaves_nothing_behind(store):
    with pytest.raises(NotFoundError):
        store.set_secrets("ghost", {"secret": "x"})
    with get_engine().connect() as conn:
        assert conn.execute(select(connector_secrets)).all() == []


# ── cross-process serialisation ──────────────────────────────────────────────────────────


def _first_statement(db_engine, run) -> str:
    from sqlalchemy import event

    statements: list[str] = []

    def record(conn, cursor, statement, *rest):
        statements.append(statement)

    event.listen(db_engine, "before_cursor_execute", record)
    try:
        run()
    finally:
        event.remove(db_engine, "before_cursor_execute", record)
    return statements[0]


def _writer_lock_statement(db_engine) -> str:
    """The cross-process writer lock each dialect takes: SQLite the database writer lock,
    Postgres the connector row's advisory transaction lock."""
    if db_engine.dialect.name == "sqlite":
        return "BEGIN IMMEDIATE"
    return "SELECT pg_advisory_xact_lock(%(key)s)"


def test_the_mcp_refresh_transaction_takes_the_writer_lock_before_it_reads(store, db_engine):
    """The writer lock first (``BEGIN IMMEDIATE`` / the row advisory lock): a compare-and-swap
    that began with a plain deferred read could lose a concurrent process's refresh."""
    from backend.db.mcp_client_store import McpClientStore

    def run():
        with McpClientStore().transaction("github") as conn:
            store.get("mcp:github", conn=conn)

    assert _first_statement(db_engine, run) == _writer_lock_statement(db_engine)


def _email():
    from backend.db.email_config_store import EmailConfigStore

    EmailConfigStore().update({"address": "me@example.com"})


def _telegram():
    from backend.db.telegram_config_store import TelegramConfigStore

    TelegramConfigStore().update({"bot_token": "t"})


def _webhook():
    from backend.db.channel_config_store import ChannelConfigStore

    ChannelConfigStore().update({"url": "https://hooks.example.com/x"})


def _session_map():
    from backend.connectors import session_map

    session_map.set_effort("telegram", "42", "high")


@pytest.mark.parametrize("write", [_email, _telegram, _webhook, _session_map])
def test_the_settings_facades_write_under_the_writer_lock(store, db_engine, write):
    assert _first_statement(db_engine, write) == _writer_lock_statement(db_engine)
