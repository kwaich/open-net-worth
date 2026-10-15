"""GET /api/connectors/{id}/secret/{field} — the operator's read-back of a stored credential.

Service unit tests + TestClient endpoint smoke for
``backend/services/connector_secrets.py`` and ``backend/api/connector_secrets.py``.

The companion file ``test_connector_secrets_model_isolation.py`` holds the OTHER half of
this feature's contract — that nothing revealed here can reach a model-facing tool output.
Both are load-bearing; this one alone would let the feature ship with the boundary broken.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.core import config, security
from backend.core.errors import NotFoundError
from backend.services import connector_secrets

# The whole surface, in one table: (connector_id, field, the value we seed). Every row is a
# field some connector form actually renders — if a form grows a credential input, it belongs
# here and in the catalog's ``revealable`` secret fields.
_CASES = [
    ("email", "password", "imap-p4ss"),
    ("webhook", "url", "https://hooks.example.test/T000/B000/hook-url-t0ken"),
    ("webhook", "secret", "hook-s3cret"),
    ("telegram", "bot_token", "123456:BOT-TOKEN"),
    ("plaid", "secret", "plaid-s3cret"),
    ("enable_banking", "private_key", "-----BEGIN PRIVATE KEY-----\nabc\n-----END-----"),
    ("yodlee", "secret", "yodlee-cobrand-pw"),
    ("yodlee", "password", "yodlee-user-pw"),
    ("alpaca", "api_key_id", "PKTESTKEYID"),
    ("alpaca", "secret_key", "alpaca-s3cret"),
    ("ibkr", "flex_token", "111122223333444455556666"),
    ("web_search:tavily", "api_key", "tvly-abc123"),
    ("coingecko", "api_key", "cg-demo-secret"),
    ("mcp:demo", "token", "mcp-bearer-token"),
    ("mcp:demo", "auth_header", "X-Demo-Key"),
    ("mcp:demo", "command", "npx"),
    ("mcp:demo", "args", "-y @vendor/server postgresql://u:mcp-dsn-pw@db/finance"),
]


# An MCP server's endpoint (command / args) and header NAME are shown to the operator in the
# list exactly as they were before the connectors table existed: the Connectors screen renders
# them in its trust panel and prefills the edit form from them. They never reach a model (the
# model-isolation tests); everything else here must stay out.
_MCP_ENDPOINT_DISPLAY = {"command", "args", "auth_header"}


def _seed_all() -> None:
    """Put one distinctive value in every revealable field of every connector."""
    from backend.db.alpaca_config_store import AlpacaConfigStore
    from backend.db.channel_config_store import ChannelConfigStore
    from backend.db.email_config_store import EmailConfigStore
    from backend.db.enable_banking_config_store import EnableBankingConfigStore
    from backend.db.ibkr_config_store import IbkrConfigStore
    from backend.db.market_data_config_store import MarketDataConfigStore
    from backend.db.mcp_client_store import McpClientStore
    from backend.db.plaid_config_store import PlaidConfigStore
    from backend.db.search_config_store import SearchConfigStore
    from backend.db.telegram_config_store import TelegramConfigStore
    from backend.db.yodlee_config_store import YodleeConfigStore
    from backend.models.mcp_client import McpServerConfig

    values = {(c, f): v for c, f, v in _CASES}
    EmailConfigStore().update({"password": values[("email", "password")]})
    ChannelConfigStore().update(
        {"url": values[("webhook", "url")], "secret": values[("webhook", "secret")]}
    )
    TelegramConfigStore().update({"bot_token": values[("telegram", "bot_token")]})
    PlaidConfigStore().update({"secret": values[("plaid", "secret")]})
    EnableBankingConfigStore().update({"private_key": values[("enable_banking", "private_key")]})
    YodleeConfigStore().update(
        {
            "secret": values[("yodlee", "secret")],
            "password": values[("yodlee", "password")],
        }
    )
    AlpacaConfigStore().update(
        {
            "api_key_id": values[("alpaca", "api_key_id")],
            "secret_key": values[("alpaca", "secret_key")],
        }
    )
    IbkrConfigStore().update({"flex_token": values[("ibkr", "flex_token")]})
    SearchConfigStore().update_provider(
        "tavily", {"api_key": values[("web_search:tavily", "api_key")]}
    )
    MarketDataConfigStore().update({"coingecko_api_key": values[("coingecko", "api_key")]})
    McpClientStore().create(
        McpServerConfig(
            id="demo",
            name="Demo",
            transport="stdio",
            command=values[("mcp:demo", "command")],
            args=values[("mcp:demo", "args")].split(" "),
            env={"API_KEY": "env-sk-1", "REGION": "eu"},
            auth_header=values[("mcp:demo", "auth_header")],
            token=values[("mcp:demo", "token")],
        )
    )


@pytest.fixture
def seeded(db_isolated):
    """A fresh database with every connector credential filled in."""
    _seed_all()
    return db_isolated


@pytest.fixture
def client(db_isolated):
    from backend.main import app

    return TestClient(app)


# ── the service ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize("connector_id,field,expected", _CASES)
def test_reveal_returns_the_saved_value(seeded, connector_id, field, expected):
    assert connector_secrets.reveal_secret(connector_id, field) == expected


def test_mcp_endpoint_parts_reveal_as_the_form_texts(seeded):
    """The args reveal as the one space-joined line the form's box takes."""
    assert connector_secrets.reveal_secret("mcp:demo", "args").startswith("-y @vendor/server ")


def test_mcp_env_reveals_as_the_key_equals_value_text_the_form_accepts(seeded):
    """The edit form's env box is ONE textarea, so ``env`` reveals as one value — sorted, so
    a reveal the user does not touch compares equal to itself on the next render."""
    assert connector_secrets.reveal_secret("mcp:demo", "env") == "API_KEY=env-sk-1\nREGION=eu"


def test_unknown_connector_raises_not_found(seeded):
    with pytest.raises(NotFoundError):
        connector_secrets.reveal_secret("nope", "token")


def test_unknown_mcp_server_raises_not_found(seeded):
    with pytest.raises(NotFoundError):
        connector_secrets.reveal_secret("mcp:not-installed", "token")


def test_a_hidden_secret_is_never_revealable(seeded):
    """Telegram's chat map is stored beside the bot token and must never come back out."""
    from backend.connectors import session_map

    session_map.get_or_create_session("telegram", "1")
    with pytest.raises(NotFoundError):
        connector_secrets.reveal_secret("telegram", "sessions")
    assert "sessions" not in connector_secrets.revealable_fields("telegram")


def test_unknown_web_search_provider_raises_not_found(seeded):
    with pytest.raises(NotFoundError):
        connector_secrets.reveal_secret("web_search:not-a-provider", "api_key")


def test_a_field_of_a_different_connector_is_not_revealable(seeded):
    """The allowlist is per connector, not a global field vocabulary: ``secret`` is real on
    plaid and meaningless on telegram, and asking telegram for it must not fall through to
    some other store's value."""
    with pytest.raises(NotFoundError) as exc:
        connector_secrets.reveal_secret("telegram", "secret")
    assert "revealable" in str(exc.value)


def test_a_stored_but_unlisted_field_is_not_revealable(seeded):
    """``access_token`` IS in Plaid's config and IS a credential — it is deliberately absent
    from the allowlist because no form renders it. A field being sensitive is not what makes
    it revealable; being an input the operator typed is."""
    from backend.db.plaid_config_store import PlaidConfigStore

    PlaidConfigStore().update({"access_token": "access-sandbox-xyz"})
    with pytest.raises(NotFoundError):
        connector_secrets.reveal_secret("plaid", "access_token")


def test_nothing_saved_is_not_found(db_isolated):
    """A real connector with an empty credential — the fresh-instance case."""
    with pytest.raises(NotFoundError) as exc:
        connector_secrets.reveal_secret("telegram", "bot_token")
    assert "no saved" in str(exc.value)


def test_a_whitespace_only_value_counts_as_nothing_saved(db_isolated):
    from backend.db.telegram_config_store import TelegramConfigStore

    TelegramConfigStore().update({"enabled": True, "bot_token": "   "})
    with pytest.raises(NotFoundError):
        connector_secrets.reveal_secret("telegram", "bot_token")


def test_revealable_fields_lists_the_allowlist(seeded):
    assert connector_secrets.revealable_fields("yodlee") == ("secret", "password")
    assert connector_secrets.revealable_fields("mcp:demo") == (
        "url",
        "command",
        "args",
        "env",
        "auth_header",
        "token",
    )
    assert connector_secrets.revealable_fields("yahoo_finance") == ()
    with pytest.raises(NotFoundError):
        connector_secrets.revealable_fields("nope")


# ── the endpoint ─────────────────────────────────────────────────────────────


def test_endpoint_returns_the_value(client):
    _seed_all()
    res = client.get("/api/connectors/telegram/secret/bot_token")
    assert res.status_code == 200
    assert res.json() == {
        "connector_id": "telegram",
        "field": "bot_token",
        "value": "123456:BOT-TOKEN",
    }


def test_endpoint_sets_cache_control_no_store(client):
    """A secret in a browser or proxy cache outlives the moment it was needed."""
    _seed_all()
    res = client.get("/api/connectors/ibkr/secret/flex_token")
    assert "no-store" in res.headers.get("cache-control", "").lower()


def test_endpoint_404s_an_unknown_connector(client):
    assert client.get("/api/connectors/nope/secret/token").status_code == 404


def test_endpoint_404s_an_unlisted_field(client):
    _seed_all()
    assert client.get("/api/connectors/telegram/secret/password").status_code == 404


def test_endpoint_404s_when_nothing_is_saved(client):
    assert client.get("/api/connectors/telegram/secret/bot_token").status_code == 404


def test_endpoint_reaches_a_colon_namespaced_connector_id(client):
    """``mcp:<id>`` and ``web_search:<id>`` carry a colon — a path segment may hold one, but
    a route that assumed otherwise would 404 exactly the two families with the most secrets."""
    _seed_all()
    assert client.get("/api/connectors/mcp:demo/secret/token").json()["value"] == (
        "mcp-bearer-token"
    )
    assert client.get("/api/connectors/web_search:tavily/secret/api_key").json()["value"] == (
        "tvly-abc123"
    )


def test_a_percent_encoded_connector_id_resolves(client):
    """The shape the BROWSER actually sends. `connectors/secrets.ts` runs the id through
    `encodeURIComponent` (the house style, matching `permissions.ts`), so a colon leaves as
    `%3A` — this pins that the route still resolves it rather than 404ing every MCP and
    web-search connector in production while the unencoded test above stays green."""
    _seed_all()
    res = client.get("/api/connectors/mcp%3Ademo/secret/token")
    assert res.status_code == 200
    assert res.json()["value"] == "mcp-bearer-token"


def test_secret_route_wins_over_the_connector_path_route(client):
    """ROUTE ORDER GUARD. ``connectors_api`` owns ``/api/connectors/{connector_id:path}`` and
    ``:path`` matches slashes, so if ``backend/main.py`` ever includes it first this request
    resolves as ``connector_id="telegram/secret/bot_token"`` → a 404 from the WRONG handler
    and a dead reveal button in every connector form."""
    _seed_all()
    res = client.get("/api/connectors/telegram/secret/bot_token")
    assert res.status_code == 200, "the {connector_id:path} route swallowed the secret route"
    assert res.json()["field"] == "bot_token"


def test_the_connector_list_still_carries_no_secret(client):
    """The reveal endpoint exists SO THAT the list stays clean. If a value ever appears here
    the split has collapsed and every cache, log and screenshot holds a credential."""
    _seed_all()
    body = client.get("/api/connectors").text
    for connector_id, field, value in _CASES:
        if connector_id == "mcp:demo" and field in _MCP_ENDPOINT_DISPLAY:
            continue
        assert value not in body, (connector_id, field)


# ── auth ─────────────────────────────────────────────────────────────────────


def test_endpoint_requires_auth(tmp_path, monkeypatch, db_isolated):
    """Not in ``_deps._EXEMPT`` — a configured instance with loopback trust off refuses it
    exactly like ``/api/portfolio``."""
    from backend.db.auth_store import AuthStore

    for key in ("AUTH_DISABLED", "AUTH_TRUST_LOOPBACK", "AUTH_PASSWORD", "AUTH_PASSWORD_HASH"):
        monkeypatch.delenv(key, raising=False)
    config._settings = None
    try:
        _seed_all()
        AuthStore().save(
            {
                "password_hash": security.hash_password("pw"),
                "session_signing_key": "key-xyz",
                "api_token": "tok-abc",
            }
        )
        monkeypatch.setenv("AUTH_PASSWORD_HASH", "x")
        monkeypatch.setenv("AUTH_TRUST_LOOPBACK", "false")
        config._settings = None

        from backend.main import app

        res = TestClient(app).get("/api/connectors/telegram/secret/bot_token")
        assert res.status_code == 401
        assert "123456:BOT-TOKEN" not in res.text
    finally:
        config._settings = None
