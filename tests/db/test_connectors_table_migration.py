"""``202610101200_connectors_table`` — every old settings row lands as one connector row.

Convention mirrors tests/db/test_data_hygiene_cleanup_migration.py: migrate a fresh sqlite file
to the revision before this one, seed with a PLAIN ``create_engine`` connection, upgrade, and
assert on the resulting rows. The previous-head template is built once per module and copied.
"""

from __future__ import annotations

import importlib
import json
import shutil

import pytest
from alembic import command
from sqlalchemy import create_engine, inspect, text

from backend.db.alembic_config import make_config

_FROM = "202610071700_session_transcript_rev"
_TARGET = "202610101200_connectors_table"


@pytest.fixture(scope="module")
def previous_head(tmp_path_factory):
    path = tmp_path_factory.mktemp("connectors-migration") / "template.db"
    command.upgrade(make_config(f"sqlite:///{path}"), _FROM)
    return path


@pytest.fixture
def legacy(previous_head, tmp_path):
    path = tmp_path / "onw.db"
    shutil.copyfile(previous_head, path)
    url = f"sqlite:///{path}"
    engine = create_engine(url)
    yield engine, url
    engine.dispose()


def _put(conn, table: str, namespace: str, payload) -> None:
    conn.execute(
        text(f"INSERT INTO {table} (namespace, payload) VALUES (:n, :p)"),
        {"n": namespace, "p": json.dumps(payload)},
    )


def _put_mcp(conn, seq: int, server_id: str, payload) -> None:
    conn.execute(
        text("INSERT INTO mcp_clients (seq, id, payload) VALUES (:s, :i, :p)"),
        {"s": seq, "i": server_id, "p": json.dumps(payload)},
    )


def _upgrade(url: str) -> None:
    command.upgrade(make_config(url), _TARGET)


def _read(engine) -> tuple[dict, dict]:
    """(connector rows by id, secrets by connector id)."""
    with engine.connect() as conn:
        rows = {}
        for r in conn.execute(text("SELECT * FROM connectors")).mappings():
            rows[r["id"]] = {
                "kind": r["kind"],
                "name": r["name"],
                "enabled": bool(r["enabled"]),
                "config": json.loads(r["config"]),
                "state": json.loads(r["state"]),
            }
        secrets = {
            r[0]: json.loads(r[1])
            for r in conn.execute(text("SELECT connector_id, secrets FROM connector_secrets"))
        }
    return rows, secrets


def _namespaces(engine, table: str = "secret_config") -> set[str]:
    with engine.connect() as conn:
        return {r[0] for r in conn.execute(text(f"SELECT namespace FROM {table}"))}


# ── fixtures of realistic (synthetic) old-format data ───────────────────────────────────────

_IMAP = {
    "enabled": True,
    "running": False,
    "poll_interval_seconds": 600,
    "imap_host": "imap.example.com",
    "imap_port": 993,
    "imap_user": "me@example.com",
    "imap_password": "imap-pw",
    "imap_folder": "Receipts",
    "lookback_days": 14,
    "max_concurrent_parses": 3,
    "provider": "custom",
    "last_poll_at": "2026-10-01T00:00:00Z",
    "last_status": "error",
    "last_error": "login to imap://me:imap-pw@imap.example.com failed",
    "stats": {"logged": 4, "queued": 1, "skipped": 2},
    "last_scan_log": [{"x": 1}],
}
_SMTP = {
    "enabled": True,
    "smtp_host": "smtp.example.com",
    "smtp_port": 465,
    "smtp_user": "relay-user@example.com",
    "smtp_password": "smtp-pw",
    "from_addr": "alerts@example.com",
    "to_addr": "someone-else@example.com",
    "use_tls": False,
}
_WEBHOOK = {
    "enabled": True,
    "url": "https://hooks.example.com/services/T0/B0/XYZ",
    "secret_header_name": "X-Sig",
    "secret": "wh-secret",
}


def _seed_everything(conn) -> None:
    _put(conn, "secret_config", "auth", {"password_hash": "hash", "api_token": "onw_tok"})
    _put(conn, "secret_config", "model_config", {"providers": {"anthropic": {"api_key": "ak"}}})
    _put(conn, "secret_config", "email_ingest", _IMAP)
    _put(conn, "secret_config", "channels_config", {"email": _SMTP, "webhook": _WEBHOOK})
    _put(
        conn,
        "secret_config",
        "telegram_config",
        {
            "enabled": True,
            "bot_token": "123:bot",
            "allowed_chat_ids": ["111", "222"],
            "access_policy": "allowlist",
            "streaming": False,
            "suggestions": True,
            "tool_stream": False,
        },
    )
    _put(
        conn,
        "secret_config",
        "connector_sessions",
        {"telegram:111": "sess_a", "telegram:111:effort": "high"},
    )
    _put(
        conn,
        "secret_config",
        "plaid_config",
        {
            "enabled": True,
            "client_id": "cid",
            "secret": "plaid-secret",
            "environment": "production",
            "access_token": "access-prod-1",
            "item_id": "item-1",
            "last_synced_at": "2026-10-02T00:00:00Z",
            "last_status": "ok",
            "last_error": None,
        },
    )
    _put(
        conn,
        "secret_config",
        "simplefin_config",
        {
            "enabled": True,
            "access_url": "https://user1:pass1@bridge.simplefin.org/simplefin",
            "last_status": "connect_error",
            "last_error": "GET https://user1:pass1@bridge.simplefin.org/simplefin/accounts failed",
        },
    )
    _put(conn, "secret_config", "lunch_money_config", {"enabled": False, "access_token": "lm"})
    _put(
        conn,
        "secret_config",
        "lunch_flow_config",
        {
            "enabled": True,
            "client_id": "lf-client",
            "client_secret": "lf-secret",
            "email": "me@example.com",
            "access_token": "lf-at",
            "refresh_token": "lf-rt",
            "token_expires_at": 1_800_000_000.5,
            "user_id": "u1",
            "last_status": "ok",
            "last_caution": "polarity looks backwards",
            "accounts_meta": {"7": {"connection_id": 3, "institution_name": "Bank", "name": "Chk"}},
        },
    )
    _put(
        conn,
        "secret_config",
        "ynab_config",
        {
            "enabled": True,
            "access_token": "ynab-tok",
            "budget_id": "b1",
            "budget_name": "Home",
            "budget_currency": "USD",
            "last_status": "ok",
        },
    )
    _put(
        conn,
        "secret_config",
        "enable_banking_config",
        {
            "enabled": True,
            "application_id": "app-1",
            "private_key": "-----BEGIN PRIVATE KEY-----\nabc\n-----END-----\n",
            "aspsp": "Nordea",
            "country": "FI",
            "session_id": "sess-eb",
        },
    )
    _put(
        conn,
        "secret_config",
        "yodlee_config",
        {
            "enabled": False,
            "client_id": "y-client",
            "secret": "y-secret",
            "api_endpoint": "production",
            "username": "yuser",
            "password": "ypw",
            "cobrand_session_token": "cob",
            "user_session_token": "usr",
        },
    )
    _put(
        conn,
        "secret_config",
        "alpaca_config",
        {
            "enabled": True,
            "api_key_id": "PKID",
            "secret_key": "PKSECRET",
            "environment": "live",
            "cash": 1200.5,
            "buying_power": 2400.0,
            "last_status": "ok",
        },
    )
    _put(
        conn,
        "secret_config",
        "ibkr_config",
        {
            "enabled": True,
            "flex_token": "flex-1",
            "flex_query_id": "99",
            "base_url": "https://flex.example.com",
        },
    )
    _put(
        conn,
        "secret_config",
        "search_config",
        {
            "default_provider": "tavily",
            "providers": {
                "tavily": {"enabled": True, "api_key": "tv-key"},
                "google_pse": {"enabled": False, "api_key": "g-key", "cx": "cx-1"},
                "duckduckgo": {"enabled": True},
                "bogus": {"enabled": True, "api_key": "dropped"},
            },
        },
    )
    _put(
        conn,
        "secret_config",
        "market_data_config",
        {
            "coingecko_api_key": "cg-key",
            "coingecko_api_key_tier": "pro",
            "yahoo_primary_for_major_crypto": False,
        },
    )
    for orphan in ("slack_config", "discord_config", "whatsapp_inbound_config"):
        _put(conn, "secret_config", orphan, {"bot_token": "orphan"})
    _put(conn, "secret_config", "gocardless_config", {"secret_id": "orphan"})
    _put(conn, "secret_config", "pluggy_config", {"client_secret": "orphan"})

    _put(conn, "runtime_state", "harness_state", {"cooldown": 1})
    _put(conn, "runtime_state", "coingecko_quota", {"calls": [1.0, 2.0], "failure_streak": 1})
    _put(conn, "runtime_state", "market_provider_circuit:yahoo_history", {"failure_streak": 2})
    _put(conn, "runtime_state", "market_provider_circuit:yahoo_health", {"last_ok_at": 5.0})
    _put(conn, "runtime_state", "market_provider_circuit:unknown_vendor", {"failure_streak": 9})
    _put(
        conn,
        "runtime_state",
        "market_data_observability",
        {
            "events": [
                {"at": 1.0, "event": "provider_request", "provider": "coingecko"},
                {"at": 2.0, "event": "provider_request", "provider": "yahoo"},
                {"at": 3.0, "event": "stale_serve"},
            ],
            "buckets": [
                {
                    "minute": 0,
                    "counts": {"provider_request": 2, "stale_serve": 1},
                    "providers": {"provider_request": {"coingecko": 1, "yahoo": 1}},
                    "latency": {"yahoo": {"samples": 1, "total_ms": 9.0, "max_ms": 9.0}},
                },
                {"minute": 60, "counts": {"stale_serve": 1}, "providers": {}, "latency": {}},
            ],
        },
    )

    _put_mcp(
        conn,
        0,
        "github",
        {
            "id": "github",
            "name": "GitHub",
            "transport": "http",
            "url": "https://mcp.example.com/mcp?api_key=URLKEY",
            "auth_type": "oauth",
            "auth_header": "Authorization",
            "token": "",
            "enabled": True,
            "read_only": False,
            "tool_allowlist": ["search"],
            "discovered_tools": [{"name": "search", "description": "Search"}],
            "server_info_name": "gh",
            "server_info_version": "1.2",
            "server_info_instructions": "Use carefully.",
            "publisher": "Example Inc",
            "homepage_url": "https://example.com",
            "repo_url": None,
            "verification": "verified",
            "last_status": "ok",
            "last_error": "refresh via https://client:cs@auth.example.com failed",
            "oauth": {
                "issuer": "https://auth.example.com/",
                "resource": "https://mcp.example.com/mcp",
                "authorization_endpoint": "https://auth.example.com/authorize",
                "token_endpoint": "https://auth.example.com/token",
                "revocation_endpoint": "",
                "client_id": "dcr-id",
                "client_secret": "dcr-secret",
                "token_endpoint_auth_method": "client_secret_basic",
                "scope": "mcp:tools",
                "access_token": "oauth-at",
                "refresh_token": "oauth-rt",
                "expires_at": 1_800_000_000.0,
                "obtained_at": 1_799_996_400.0,
            },
        },
    )
    _put_mcp(
        conn,
        1,
        "pg",
        {
            "id": "pg",
            "name": "",
            "transport": "stdio",
            "command": "npx",
            "args": ["-y", "pg-mcp", "postgresql://u:pw@db/x"],
            "env": {"PGPASSWORD": "pw"},
            "auth_type": "none",
            "auth_header": "Authorization",
            "enabled": False,
        },
    )


@pytest.fixture(scope="module")
def migrated(previous_head, tmp_path_factory):
    path = tmp_path_factory.mktemp("connectors-migrated") / "onw.db"
    shutil.copyfile(previous_head, path)
    url = f"sqlite:///{path}"
    engine = create_engine(url)
    with engine.begin() as conn:
        _seed_everything(conn)
    _upgrade(url)
    rows, secrets = _read(engine)
    yield engine, rows, secrets
    engine.dispose()


# ── the full migration ──────────────────────────────────────────────────────────────────────


def test_every_old_row_becomes_exactly_these_connectors(migrated):
    _, rows, _ = migrated
    assert set(rows) == {
        "email",
        "webhook",
        "telegram",
        "simplefin",
        "plaid",
        "lunch_money",
        "lunch_flow",
        "ynab",
        "enable_banking",
        "yodlee",
        "alpaca",
        "ibkr",
        "web_search:tavily",
        "web_search:google_pse",
        "web_search:duckduckgo",
        "coingecko",
        "yahoo_finance",
        "mcp:github",
        "mcp:pg",
    }


def test_email_is_one_connector_with_one_identity(migrated):
    _, rows, secrets = migrated
    assert rows["email"] == {
        "kind": "email",
        "name": "Email",
        "enabled": True,
        "config": {
            "provider": "custom",
            "address": "me@example.com",
            "imap_host": "imap.example.com",
            "imap_port": 993,
            "imap_folder": "Receipts",
            "lookback_days": 14,
            "smtp_host": "smtp.example.com",
            "smtp_port": 465,
            "use_tls": False,
        },
        "state": {
            "last_status": "error",
            "last_error": "login to imap://***@imap.example.com failed",
        },
    }
    assert secrets["email"] == {"password": "imap-pw"}


def test_webhook_url_and_secret_are_secrets(migrated):
    _, rows, secrets = migrated
    assert rows["webhook"] == {
        "kind": "webhook",
        "name": "Webhook",
        "enabled": True,
        "config": {"secret_header_name": "X-Sig"},
        "state": {},
    }
    assert secrets["webhook"] == {"url": _WEBHOOK["url"], "secret": "wh-secret"}


def test_telegram_keeps_its_session_map_as_a_secret(migrated):
    _, rows, secrets = migrated
    assert rows["telegram"] == {
        "kind": "telegram",
        "name": "Telegram",
        "enabled": True,
        "config": {
            "allowed_chat_ids": ["111", "222"],
            "access_policy": "allowlist",
            "streaming": False,
            "suggestions": True,
            "tool_stream": False,
        },
        "state": {},
    }
    assert secrets["telegram"] == {
        "bot_token": "123:bot",
        "sessions": {"telegram:111": "sess_a", "telegram:111:effort": "high"},
    }


def test_plaid_splits_into_config_secrets_and_state(migrated):
    _, rows, secrets = migrated
    assert rows["plaid"] == {
        "kind": "plaid",
        "name": "Plaid",
        "enabled": True,
        "config": {"client_id": "cid", "environment": "production"},
        "state": {"last_synced_at": "2026-10-02T00:00:00Z", "last_status": "ok"},
    }
    assert secrets["plaid"] == {
        "secret": "plaid-secret",
        "access_token": "access-prod-1",
        "item_id": "item-1",
    }


def test_simplefin_access_url_is_a_secret_and_never_in_state(migrated):
    _, rows, secrets = migrated
    assert secrets["simplefin"] == {
        "access_url": "https://user1:pass1@bridge.simplefin.org/simplefin"
    }
    assert rows["simplefin"]["config"] == {}
    assert rows["simplefin"]["state"] == {
        "last_status": "connect_error",
        "last_error": "GET https://***@bridge.simplefin.org/simplefin/accounts failed",
    }
    assert "pass1" not in json.dumps(rows)


def test_lunch_flow_minted_tokens_are_secrets_and_its_cache_is_state(migrated):
    _, rows, secrets = migrated
    assert rows["lunch_flow"]["config"] == {"client_id": "lf-client", "email": "me@example.com"}
    assert rows["lunch_flow"]["state"] == {
        "token_expires_at": 1_800_000_000.5,
        "user_id": "u1",
        "last_status": "ok",
        "last_caution": "polarity looks backwards",
        "accounts_meta": {"7": {"connection_id": 3, "institution_name": "Bank", "name": "Chk"}},
    }
    assert secrets["lunch_flow"] == {
        "client_secret": "lf-secret",
        "access_token": "lf-at",
        "refresh_token": "lf-rt",
    }


@pytest.mark.parametrize(
    "cid, enabled, config, state, secrets_",
    [
        ("lunch_money", False, {}, {}, {"access_token": "lm"}),
        (
            "ynab",
            True,
            {"budget_id": "b1"},
            {"budget_name": "Home", "budget_currency": "USD", "last_status": "ok"},
            {"access_token": "ynab-tok"},
        ),
        (
            "enable_banking",
            True,
            {"application_id": "app-1", "aspsp": "Nordea", "country": "FI"},
            {},
            {
                "private_key": "-----BEGIN PRIVATE KEY-----\nabc\n-----END-----\n",
                "session_id": "sess-eb",
            },
        ),
        (
            "yodlee",
            False,
            {"client_id": "y-client", "api_endpoint": "production", "username": "yuser"},
            {},
            {
                "secret": "y-secret",
                "password": "ypw",
                "cobrand_session_token": "cob",
                "user_session_token": "usr",
            },
        ),
        (
            "alpaca",
            True,
            {"environment": "live"},
            {"cash": 1200.5, "buying_power": 2400.0, "last_status": "ok"},
            {"api_key_id": "PKID", "secret_key": "PKSECRET"},
        ),
        (
            "ibkr",
            True,
            {"flex_query_id": "99", "base_url": "https://flex.example.com"},
            {},
            {"flex_token": "flex-1"},
        ),
    ],
)
def test_remaining_aggregators(migrated, cid, enabled, config, state, secrets_):
    _, rows, secrets = migrated
    assert rows[cid]["enabled"] is enabled
    assert rows[cid]["config"] == config
    assert rows[cid]["state"] == state
    assert secrets[cid] == secrets_


def test_web_search_is_one_row_per_provider_with_one_default(migrated):
    _, rows, secrets = migrated
    assert rows["web_search:tavily"] == {
        "kind": "web_search",
        "name": "Tavily",
        "enabled": True,
        "config": {"provider": "tavily", "default": True},
        "state": {},
    }
    assert rows["web_search:google_pse"]["config"] == {
        "provider": "google_pse",
        "default": False,
        "cx": "cx-1",
    }
    assert rows["web_search:google_pse"]["name"] == "Google Programmable Search"
    assert rows["web_search:google_pse"]["enabled"] is False
    assert rows["web_search:duckduckgo"]["enabled"] is True
    assert secrets["web_search:tavily"] == {"api_key": "tv-key"}
    assert secrets["web_search:google_pse"] == {"api_key": "g-key"}
    assert "web_search:duckduckgo" not in secrets
    assert "web_search:bogus" not in rows


def test_market_data_rows_carry_key_tier_and_health(migrated):
    _, rows, secrets = migrated
    assert rows["coingecko"]["enabled"] is True
    assert rows["coingecko"]["config"] == {"tier": "pro"}
    assert secrets["coingecko"] == {"api_key": "cg-key"}
    assert rows["yahoo_finance"]["enabled"] is True
    assert rows["yahoo_finance"]["config"] == {"primary_for_major_crypto": False}
    assert "yahoo_finance" not in secrets

    assert rows["coingecko"]["state"]["quota"] == {"calls": [1.0, 2.0], "failure_streak": 1}
    assert rows["yahoo_finance"]["state"]["circuit"] == {
        "yahoo_history": {"failure_streak": 2},
        "yahoo_health": {"last_ok_at": 5.0},
    }
    assert "observability" not in rows["coingecko"]["state"]
    assert "observability" not in rows["yahoo_finance"]["state"]


def test_price_source_telemetry_stays_in_its_runtime_state_row(migrated):
    """The event log is large and written on every provider call: it is not moved into the
    ``connectors`` rows, and its ``runtime_state`` row is left exactly as it was."""
    engine, _, _ = migrated
    with engine.connect() as conn:
        doc = json.loads(
            conn.execute(
                text(
                    "SELECT payload FROM runtime_state WHERE namespace='market_data_observability'"
                )
            ).scalar()
        )
    assert [e["at"] for e in doc["events"]] == [1.0, 2.0, 3.0]
    assert [b["minute"] for b in doc["buckets"]] == [0, 60]


def test_mcp_oauth_server_splits_credentials_from_metadata(migrated):
    _, rows, secrets = migrated
    assert rows["mcp:github"] == {
        "kind": "mcp",
        "name": "GitHub",
        "enabled": True,
        "config": {
            "transport": "http",
            "auth_type": "oauth",
            "read_only": False,
            "tool_allowlist": ["search"],
            "verification": "verified",
            "publisher": "Example Inc",
            "homepage_url": "https://example.com",
        },
        "state": {
            "discovered_tools": [{"name": "search", "description": "Search"}],
            "server_info_name": "gh",
            "server_info_version": "1.2",
            "server_info_instructions": "Use carefully.",
            "last_status": "ok",
            "last_error": "refresh via https://***@auth.example.com failed",
            "oauth": {
                "issuer": "https://auth.example.com/",
                "resource": "https://mcp.example.com/mcp",
                "authorization_endpoint": "https://auth.example.com/authorize",
                "token_endpoint": "https://auth.example.com/token",
                "revocation_endpoint": "",
                "client_id": "dcr-id",
                "token_endpoint_auth_method": "client_secret_basic",
                "scope": "mcp:tools",
                "expires_at": 1_800_000_000.0,
                "obtained_at": 1_799_996_400.0,
            },
        },
    }
    assert secrets["mcp:github"] == {
        "url": "https://mcp.example.com/mcp?api_key=URLKEY",
        "auth_header": "Authorization",
        "oauth_client_secret": "dcr-secret",
        "oauth_access_token": "oauth-at",
        "oauth_refresh_token": "oauth-rt",
    }


def test_mcp_stdio_server_keeps_command_args_and_env_as_secrets(migrated):
    _, rows, secrets = migrated
    assert rows["mcp:pg"]["name"] == "pg"
    assert rows["mcp:pg"]["enabled"] is False
    assert rows["mcp:pg"]["config"]["transport"] == "stdio"
    assert secrets["mcp:pg"] == {
        "command": "npx",
        "args": ["-y", "pg-mcp", "postgresql://u:pw@db/x"],
        "env": {"PGPASSWORD": "pw"},
        "auth_header": "Authorization",
    }


def test_no_credential_value_appears_in_the_readable_table(migrated):
    engine, _, secrets = migrated
    with engine.connect() as conn:
        readable = json.dumps(
            [dict(r) for r in conn.execute(text("SELECT * FROM connectors")).mappings()]
        )
    needles = []
    for fields in secrets.values():
        for value in fields.values():
            needles += (
                value
                if isinstance(value, list)
                else list(value.values())
                if isinstance(value, dict)
                else [value]
            )
    credentials = [
        n for n in needles if isinstance(n, str) and len(n) >= 6 and n != "Authorization"
    ]
    assert credentials
    assert not [n for n in credentials if n in readable]


def test_old_namespaces_are_gone_and_unrelated_ones_untouched(migrated):
    engine, _, _ = migrated
    assert _namespaces(engine) == {"auth", "model_config"}
    assert _namespaces(engine, "runtime_state") == {"harness_state", "market_data_observability"}
    assert "mcp_clients" not in inspect(engine).get_table_names()
    with engine.connect() as conn:
        auth = json.loads(
            conn.execute(text("SELECT payload FROM secret_config WHERE namespace='auth'")).scalar()
        )
    assert auth == {"password_hash": "hash", "api_token": "onw_tok"}


def test_downgrade_is_refused(migrated):
    engine, _, _ = migrated
    with pytest.raises(NotImplementedError):
        command.downgrade(make_config(engine.url.render_as_string(hide_password=False)), "-1")


# ── the edges ───────────────────────────────────────────────────────────────────────────────


def test_a_fresh_install_still_gets_the_two_price_sources(legacy):
    engine, url = legacy
    _upgrade(url)

    rows, secrets = _read(engine)

    assert rows == {
        "coingecko": {
            "kind": "coingecko",
            "name": "CoinGecko",
            "enabled": True,
            "config": {"tier": "demo"},
            "state": {},
        },
        "yahoo_finance": {
            "kind": "yahoo_finance",
            "name": "Yahoo Finance",
            "enabled": True,
            "config": {"primary_for_major_crypto": True},
            "state": {},
        },
    }
    assert secrets == {}


def _email_only(imap: dict | None, smtp: dict | None, engine, url):
    with engine.begin() as conn:
        if imap is not None:
            _put(conn, "secret_config", "email_ingest", imap)
        if smtp is not None:
            _put(conn, "secret_config", "channels_config", {"email": smtp})
    _upgrade(url)
    return _read(engine)


@pytest.mark.parametrize(
    "imap_enabled, smtp_enabled, expected",
    [(True, True, True), (True, False, False), (False, True, False), (False, False, False)],
)
def test_email_is_enabled_only_when_every_configured_leg_was(
    legacy, imap_enabled, smtp_enabled, expected
):
    engine, url = legacy
    rows, _ = _email_only(
        {**_IMAP, "enabled": imap_enabled}, {**_SMTP, "enabled": smtp_enabled}, engine, url
    )
    assert rows["email"]["enabled"] is expected


def test_email_with_only_imap_configured_follows_the_imap_flag(legacy):
    engine, url = legacy
    rows, secrets = _email_only(_IMAP, None, engine, url)
    assert rows["email"]["enabled"] is True
    assert rows["email"]["config"]["smtp_host"] == ""
    assert secrets["email"] == {"password": "imap-pw"}


def test_email_with_only_smtp_configured_uses_the_smtp_identity(legacy):
    engine, url = legacy
    rows, secrets = _email_only(None, _SMTP, engine, url)
    assert rows["email"]["enabled"] is True
    assert rows["email"]["config"]["address"] == "relay-user@example.com"
    assert secrets["email"] == {"password": "smtp-pw"}


def test_email_with_only_a_from_address_uses_it_as_the_identity(legacy):
    engine, url = legacy
    rows, _ = _email_only(None, {**_SMTP, "smtp_user": ""}, engine, url)
    assert rows["email"]["config"]["address"] == "alerts@example.com"


def test_email_with_neither_leg_configured_is_disabled(legacy):
    engine, url = legacy
    rows, secrets = _email_only({"enabled": True, "imap_host": "h"}, {"enabled": True}, engine, url)
    assert rows["email"]["enabled"] is False
    assert "email" not in secrets


def test_the_legacy_single_provider_search_shape_is_migrated(legacy):
    engine, url = legacy
    with engine.begin() as conn:
        _put(
            conn,
            "secret_config",
            "search_config",
            {
                "enabled": True,
                "provider": "serper",
                "keys": {"serper": "sp-key", "tavily": "tv-key", "nope": "x", "perplexity": " "},
            },
        )
    _upgrade(url)

    rows, secrets = _read(engine)

    assert rows["web_search:serper"]["enabled"] is True
    assert rows["web_search:serper"]["config"] == {"provider": "serper", "default": True}
    assert rows["web_search:tavily"]["enabled"] is False
    assert rows["web_search:tavily"]["config"]["default"] is False
    assert secrets["web_search:serper"] == {"api_key": "sp-key"}
    assert secrets["web_search:tavily"] == {"api_key": "tv-key"}
    assert {r for r in rows if r.startswith("web_search:")} == {
        "web_search:serper",
        "web_search:tavily",
    }


def test_the_pre_rename_active_provider_key_still_selects_the_default(legacy):
    engine, url = legacy
    with engine.begin() as conn:
        _put(
            conn,
            "secret_config",
            "search_config",
            {
                "active_provider": "firecrawl",
                "providers": {
                    "firecrawl": {"enabled": True, "api_key": "fc"},
                    "tavily": {"enabled": True, "api_key": "tv"},
                },
            },
        )
    _upgrade(url)

    rows, _ = _read(engine)

    assert rows["web_search:firecrawl"]["config"]["default"] is True
    assert rows["web_search:tavily"]["config"]["default"] is False


def test_a_default_with_no_stored_entry_still_gets_its_row(legacy):
    engine, url = legacy
    with engine.begin() as conn:
        _put(
            conn,
            "secret_config",
            "search_config",
            {"default_provider": "duckduckgo", "providers": {"tavily": {"enabled": True}}},
        )
    _upgrade(url)

    rows, secrets = _read(engine)

    assert rows["web_search:duckduckgo"]["config"] == {"provider": "duckduckgo", "default": True}
    assert rows["web_search:duckduckgo"]["name"] == "DuckDuckGo"
    assert rows["web_search:duckduckgo"]["enabled"] is True
    assert "web_search:duckduckgo" not in secrets
    assert rows["web_search:tavily"]["config"]["default"] is False


def test_a_keyed_default_with_no_stored_entry_gets_a_disabled_row(legacy):
    engine, url = legacy
    with engine.begin() as conn:
        _put(conn, "secret_config", "search_config", {"default_provider": "serper"})
    _upgrade(url)

    rows, secrets = _read(engine)

    assert rows["web_search:serper"]["config"] == {"provider": "serper", "default": True}
    assert rows["web_search:serper"]["enabled"] is False
    assert "web_search:serper" not in secrets
    assert {r for r in rows if r.startswith("web_search:")} == {"web_search:serper"}


def test_an_unknown_default_creates_no_row(legacy):
    engine, url = legacy
    with engine.begin() as conn:
        _put(conn, "secret_config", "search_config", {"default_provider": "bogus"})
    _upgrade(url)

    rows, _ = _read(engine)

    assert not any(r.startswith("web_search:") for r in rows)


def test_an_unconfigured_telegram_with_sessions_still_keeps_them(legacy):
    engine, url = legacy
    with engine.begin() as conn:
        _put(conn, "secret_config", "connector_sessions", {"telegram:1": "sess"})
    _upgrade(url)

    rows, secrets = _read(engine)

    assert rows["telegram"]["enabled"] is False
    assert rows["telegram"]["state"] == {}
    assert secrets["telegram"] == {"sessions": {"telegram:1": "sess"}}


def test_an_absent_aggregator_gets_no_row(legacy):
    engine, url = legacy
    with engine.begin() as conn:
        _put(conn, "secret_config", "ynab_config", {"enabled": True, "access_token": "t"})
    _upgrade(url)

    rows, _ = _read(engine)

    assert "ynab" in rows
    assert "plaid" not in rows and "simplefin" not in rows


@pytest.mark.parametrize(
    "table, namespace",
    [
        ("secret_config", "plaid_config"),
        ("secret_config", "email_ingest"),
        ("secret_config", "search_config"),
    ],
)
def test_a_non_object_source_payload_aborts_the_migration(legacy, table, namespace):
    engine, url = legacy
    with engine.begin() as conn:
        _put(conn, table, namespace, ["not", "an", "object"])

    with pytest.raises(RuntimeError, match="not a JSON object"):
        _upgrade(url)

    with engine.connect() as conn:
        assert namespace in _namespaces(engine)
        assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar() == _FROM


def test_a_non_object_mcp_payload_aborts_the_migration(legacy):
    engine, url = legacy
    with engine.begin() as conn:
        _put_mcp(conn, 0, "broken", "just a string")

    with pytest.raises(RuntimeError, match="not a JSON object"):
        _upgrade(url)

    with engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM mcp_clients")).scalar() == 1


def test_a_non_object_health_row_is_dropped_not_fatal(legacy):
    engine, url = legacy
    with engine.begin() as conn:
        _put(conn, "runtime_state", "coingecko_quota", "garbage")
    _upgrade(url)

    rows, _ = _read(engine)

    assert rows["coingecko"]["state"] == {}
    assert _namespaces(engine, "runtime_state") == set()


def test_mcp_servers_keep_the_order_their_old_seq_gave_them(legacy):
    """``connectors`` lists oldest first. The migration inserts every row at once, so each gets
    a ``created_at`` one microsecond after the one before it — an MCP server keeps its place
    (here: seq order, which is NOT the alphabetical or insertion order of the ids)."""
    engine, url = legacy
    with engine.begin() as conn:
        _put_mcp(conn, 2, "alpha", {"id": "alpha", "url": "https://a.example.com/mcp"})
        _put_mcp(conn, 0, "zeta", {"id": "zeta", "url": "https://z.example.com/mcp"})
        _put_mcp(conn, 1, "mid", {"id": "mid", "url": "https://m.example.com/mcp"})
    _upgrade(url)

    with engine.connect() as conn:
        listed = [
            r[0]
            for r in conn.execute(
                text("SELECT id FROM connectors WHERE kind = 'mcp' ORDER BY created_at, id")
            )
        ]
    assert listed == ["mcp:zeta", "mcp:mid", "mcp:alpha"]


def test_the_frozen_redactor_agrees_with_the_live_one():
    """The revision carries its own copy (it never imports app code); both must mask alike."""
    from backend.core.redact_error import redact_error

    migration = importlib.import_module("backend.db.alembic.versions.202610101200_connectors_table")
    samples = [
        "login to imap://me:imap-pw@imap.example.com failed",
        "https://alice:pa/ss@host/x?token=abc&sig=zzz",
        'Authorization: Bearer abcdef123456 {"api_key": "k1"}',
        "POST https://h/api/mcp/s/Zk9aN3pTQ1JvM2xYd0Y5dTZ1Rm5hS2ZP/mcp failed",
        "GET http://localhost:8080/users/x?email=a@b.com failed",
    ]
    for sample in samples:
        assert migration._redact(sample) == redact_error(sample)
