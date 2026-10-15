"""THE BOUNDARY: the operator may read a stored connector secret; the model may not.

``services/connector_secrets.py`` exists so an authenticated browser can show the operator
the key they pasted in. This file is the other half of that feature — the proof that
granting it did not widen what an LLM can see. Both halves ship together; the reveal
endpoint without this test is a feature with its safety property merely asserted.

## Why this is a separate test file, and why it is behavioural

The leak this codebase already had (``list_mcp_servers`` returning a working DSN inside
``endpoint``, ADR ``2026-08-13-what-a-model-may-see-of-a-connector``) was not caused by
anyone deciding to show a model a credential. It was caused by a projection built for the
BROWSER being reused for the MODEL, and it stayed invisible because every test asserted the
fields it expected rather than sweeping for the values it forbade.

So these tests do the opposite: seed a unique sentinel into every connector credential,
then run the model-facing surfaces and search their WHOLE serialized output for any of
them. A new field on a store, a new key in a capability config, a new tool that dumps a
``ConnectorView`` — each fails here without anyone having to remember to update an
allowlist.

``docs/adr/2026-08-14-an-operator-may-read-a-connector-secret-a-model-may-not.md``.
"""

from __future__ import annotations

import ast
import asyncio
import json
import pathlib

import pytest

from backend.models.mcp_client import McpOAuth, McpServerConfig, mcp_connector_id

# One unmistakable sentinel per credential. Distinct per field so a failure names the leak.
_SENTINELS = {
    "email_password": "SENTINEL-EMAIL-PASSWORD",
    "webhook_url": "SENTINEL-WEBHOOK-URL-TOKEN",
    "webhook_secret": "SENTINEL-WEBHOOK-SECRET",
    "bot_token": "SENTINEL-TELEGRAM-BOT-TOKEN",
    "plaid_secret": "SENTINEL-PLAID-SECRET",
    "lunch_money_token": "SENTINEL-LUNCH-MONEY-TOKEN",
    "lunch_flow_client_secret": "SENTINEL-LUNCH-FLOW-CLIENT-SECRET",
    # Machine-minted OAuth tokens — NOT operator-revealable (same policy as Plaid's own
    # access_token), but they must still never leak to a model-facing surface, so they get a
    # sentinel too even though test_every_sentinel_is_actually_stored proves their presence a
    # different way (the store's own getters, not connector_secrets.reveal_secret).
    "lunch_flow_access_token": "SENTINEL-LUNCH-FLOW-ACCESS-TOKEN",
    "lunch_flow_refresh_token": "SENTINEL-LUNCH-FLOW-REFRESH-TOKEN",
    "ynab_token": "SENTINEL-YNAB-TOKEN",
    "eb_private_key": "SENTINEL-ENABLE-BANKING-PRIVATE-KEY",
    "yodlee_secret": "SENTINEL-YODLEE-SECRET",
    "yodlee_password": "SENTINEL-YODLEE-USER-PASSWORD",
    "alpaca_key_id": "SENTINEL-ALPACA-KEY-ID",
    "alpaca_secret": "SENTINEL-ALPACA-SECRET",
    "ibkr_token": "SENTINEL-IBKR-FLEX-TOKEN",
    "search_key": "SENTINEL-TAVILY-API-KEY",
    "coingecko_key": "SENTINEL-COINGECKO-API-KEY",
    "mcp_token": "SENTINEL-MCP-BEARER-TOKEN",
    "mcp_env_value": "SENTINEL-MCP-ENV-VALUE",
    # A credential inside an MCP server's argv (the documented quick start of a database
    # server): operator-visible on the Connectors screen, but never model-visible.
    "mcp_dsn": "SENTINEL-MCP-DSN-PASSWORD",
    "mcp_oauth_access": "SENTINEL-MCP-OAUTH-ACCESS",
    "mcp_oauth_refresh": "SENTINEL-MCP-OAUTH-REFRESH",
}

_SERVER_ID = "sentinel-srv"
_CONNECTOR_ID = mcp_connector_id(_SERVER_ID)


@pytest.fixture()
def seeded(db_isolated):
    """Every connector credential set to a sentinel."""
    from backend.db.alpaca_config_store import AlpacaConfigStore
    from backend.db.channel_config_store import ChannelConfigStore
    from backend.db.email_config_store import EmailConfigStore
    from backend.db.enable_banking_config_store import EnableBankingConfigStore
    from backend.db.ibkr_config_store import IbkrConfigStore
    from backend.db.lunch_flow_config_store import LunchFlowConfigStore
    from backend.db.lunch_money_config_store import LunchMoneyConfigStore
    from backend.db.market_data_config_store import MarketDataConfigStore
    from backend.db.mcp_client_store import McpClientStore
    from backend.db.plaid_config_store import PlaidConfigStore
    from backend.db.search_config_store import SearchConfigStore
    from backend.db.telegram_config_store import TelegramConfigStore
    from backend.db.ynab_config_store import YnabConfigStore
    from backend.db.yodlee_config_store import YodleeConfigStore

    EmailConfigStore().update(
        {
            "enabled": True,
            "address": "me@example.com",
            "imap_host": "imap.example.com",
            "password": _S("email_password"),
        }
    )
    ChannelConfigStore().update(
        {
            "enabled": True,
            "url": f"https://hook.example.com/{_S('webhook_url')}",
            "secret": _S("webhook_secret"),
        }
    )
    TelegramConfigStore().update({"enabled": True, "bot_token": _S("bot_token")})
    PlaidConfigStore().update({"enabled": True, "client_id": "cid", "secret": _S("plaid_secret")})
    LunchMoneyConfigStore().update({"enabled": True, "access_token": _S("lunch_money_token")})
    LunchFlowConfigStore().update(
        {"enabled": True, "client_id": "cid", "client_secret": _S("lunch_flow_client_secret")}
    )
    LunchFlowConfigStore().set_tokens(
        access_token=_S("lunch_flow_access_token"),
        refresh_token=_S("lunch_flow_refresh_token"),
        expires_at=9_999_999_999.0,
        user_id="lf-user-1",
    )
    YnabConfigStore().update({"enabled": True, "access_token": _S("ynab_token")})
    EnableBankingConfigStore().update(
        {"enabled": True, "application_id": "app", "private_key": _S("eb_private_key")}
    )
    YodleeConfigStore().update(
        {
            "enabled": True,
            "client_id": "cid",
            "secret": _S("yodlee_secret"),
            "username": "user",
            "password": _S("yodlee_password"),
        }
    )
    AlpacaConfigStore().update(
        {"enabled": True, "api_key_id": _S("alpaca_key_id"), "secret_key": _S("alpaca_secret")}
    )
    IbkrConfigStore().update(
        {"enabled": True, "flex_query_id": "12345", "flex_token": _S("ibkr_token")}
    )
    SearchConfigStore().update_provider("tavily", {"enabled": True, "api_key": _S("search_key")})
    MarketDataConfigStore().update({"coingecko_api_key": _S("coingecko_key")})
    McpClientStore().create(
        McpServerConfig(
            id=_SERVER_ID,
            name="Sentinel server",
            transport="stdio",
            command="npx",
            args=["-y", "@vendor/server", f"postgresql://u:{_S('mcp_dsn')}@db/finance"],
            env={"API_KEY": _S("mcp_env_value")},
            token=_S("mcp_token"),
            enabled=True,
        )
    )
    McpClientStore().create(
        McpServerConfig(
            id="sentinel-oauth",
            name="Sentinel OAuth server",
            url="https://mcp.example.com",
            auth_type="oauth",
            enabled=True,
            oauth=McpOAuth(
                issuer="https://auth.example.com",
                resource="https://mcp.example.com",
                authorization_endpoint="https://auth.example.com/a",
                token_endpoint="https://auth.example.com/t",
                client_id="cid",
                access_token=_S("mcp_oauth_access"),
                refresh_token=_S("mcp_oauth_refresh"),
            ),
        )
    )
    # A discovery cache, because it is the one place third-party text lands on the view.
    McpClientStore().record_discovery(
        _SERVER_ID, [{"name": "do_thing", "description": "Does a thing."}], "ok"
    )


def _S(key: str) -> str:
    return _SENTINELS[key]


def _assert_clean(payload: object, surface: str) -> None:
    """No sentinel anywhere in ``payload``'s serialized form."""
    text = payload if isinstance(payload, str) else json.dumps(payload, default=str)
    leaked = sorted(name for name, value in _SENTINELS.items() if value in text)
    assert not leaked, f"{surface} leaked connector secret(s) to the model: {', '.join(leaked)}"


def _call(name: str, args: dict) -> str:
    from backend.ai.tools import execute_tool

    text, _proposed = asyncio.run(execute_tool(name, args))
    return text


# ── 0. the control ───────────────────────────────────────────────────────────


def test_every_sentinel_is_actually_stored(seeded):
    """WITHOUT THIS, THE WHOLE FILE IS VACUOUS. Every sweep below asserts an absence, so a
    seeding call that silently no-ops (a renamed store field, a keep-on-blank rule that ate
    the write) turns each of them into a test that cannot fail. This asserts the presence
    the others are the absence of — through the operator path, which is the one surface
    that is SUPPOSED to return these values."""
    from backend.services import connector_secrets

    stored = {
        _S("email_password"): ("email", "password"),
        _S("webhook_secret"): ("webhook", "secret"),
        _S("bot_token"): ("telegram", "bot_token"),
        _S("plaid_secret"): ("plaid", "secret"),
        _S("lunch_money_token"): ("lunch_money", "access_token"),
        _S("lunch_flow_client_secret"): ("lunch_flow", "client_secret"),
        _S("ynab_token"): ("ynab", "access_token"),
        _S("eb_private_key"): ("enable_banking", "private_key"),
        _S("yodlee_secret"): ("yodlee", "secret"),
        _S("yodlee_password"): ("yodlee", "password"),
        _S("alpaca_key_id"): ("alpaca", "api_key_id"),
        _S("alpaca_secret"): ("alpaca", "secret_key"),
        _S("ibkr_token"): ("ibkr", "flex_token"),
        _S("search_key"): ("web_search:tavily", "api_key"),
        _S("coingecko_key"): ("coingecko", "api_key"),
        _S("mcp_token"): (_CONNECTOR_ID, "token"),
    }
    for expected, (connector_id, field) in stored.items():
        assert connector_secrets.reveal_secret(connector_id, field) == expected
    assert _S("mcp_env_value") in connector_secrets.reveal_secret(_CONNECTOR_ID, "env")
    assert _S("mcp_dsn") in connector_secrets.reveal_secret(_CONNECTOR_ID, "args")
    assert _S("webhook_url") in connector_secrets.reveal_secret("webhook", "url")
    # The OAuth tokens are NOT operator-revealable (machine-minted, same policy as Plaid's
    # access_token) — proved present through the store's own getters instead.
    with pytest.raises(Exception):
        connector_secrets.reveal_secret("lunch_flow", "access_token")
    with pytest.raises(Exception):
        connector_secrets.reveal_secret("mcp:sentinel-oauth", "oauth_access_token")


def test_lunch_flow_oauth_tokens_are_genuinely_stored(seeded):
    """The control for the two sentinels reveal_secret cannot prove: without this, the
    access_token/refresh_token sweeps below would be vacuous the same way every other control
    guards against."""
    from backend.db.lunch_flow_config_store import LunchFlowConfigStore

    store = LunchFlowConfigStore()
    assert store.access_token() == _S("lunch_flow_access_token")
    assert store.refresh_token() == _S("lunch_flow_refresh_token")


# ── 1. the model-facing tool outputs ─────────────────────────────────────────


def test_list_mcp_servers_reveals_no_credential(seeded):
    """The tool the earlier leak came through. A stdio server's token AND its env values
    are both in the store row this projects from."""
    _assert_clean(_call("list_mcp_servers", {}), "list_mcp_servers")


def test_get_connector_tools_reveals_no_credential(seeded):
    for connector_id in ("email", "telegram", "ibkr", "web_search:tavily", _CONNECTOR_ID):
        _assert_clean(
            _call("get_connector_tools", {"connector_id": connector_id}),
            f"get_connector_tools({connector_id})",
        )


def test_set_connector_enabled_reveals_no_credential(seeded):
    """A WRITE tool that answers with connector state. It used to dump the whole
    ``ConnectorView``; nothing about the reveal feature may put a value back into one."""
    for connector_id in ("telegram", "ibkr", "lunch_flow", _CONNECTOR_ID):
        _assert_clean(
            _call("set_connector_enabled", {"connector_id": connector_id, "enabled": True}),
            f"set_connector_enabled({connector_id})",
        )


def test_connector_status_reveals_no_credential(seeded):
    for provider in (
        "plaid",
        "lunch_money",
        "lunch_flow",
        "ynab",
        "yodlee",
        "alpaca",
        "ibkr",
        "enable_banking",
    ):
        _assert_clean(
            _call("get_connector_status", {"connectors": [provider]}),
            f"get_connector_status({provider})",
        )
    # Omitted = EVERY connector: email, webhook, telegram, web search and the MCP server too.
    _assert_clean(_call("get_connector_status", {}), "get_connector_status()")


# ── 2. the shared serializer everything above projects from ──────────────────


def test_the_connector_view_itself_carries_no_secret(seeded):
    """``ConnectorView`` is the serializer the browser consumes and the tools project from.
    The reveal feature was built as a separate module precisely so this stays true — if a
    credential value ever lands here, the split has collapsed.

    The one deliberate exception is an MCP server's own ``command`` / ``args``: the operator's
    Connectors screen shows the endpoint it is pointed at (and prefills the edit form from it),
    exactly as before the connectors table, and ``list_mcp_servers`` / ``get_connector_status``
    / ``set_connector_enabled`` above never project it. A credential inside it is still
    swept out of every model-facing surface."""
    from backend.services import connectors as connectors_service

    views = [v.model_dump() for v in connectors_service.list_connectors()]
    for view in views:
        if view["kind"] == "mcp":
            for field in ("command", "args", "endpoint"):
                view["config"].pop(field, None)
    _assert_clean(views, "services.connectors.list_connectors()")


# ── 3. the structural guard: no AI/MCP module may reach the reveal service ───


def _modules_under(*roots: str) -> list[pathlib.Path]:
    base = pathlib.Path(__file__).resolve().parents[1] / "backend"
    return sorted(p for root in roots for p in (base / root).rglob("*.py"))


def _imports_of(path: pathlib.Path) -> set[str]:
    """Every module this file imports, however it phrases it — including a function-local
    ``from backend.services import connector_secrets``, which is the shape the AI tool
    modules use for their store imports and would therefore be the shape of a leak."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            names.add(module)
            names.update(f"{module}.{a.name}" for a in node.names)
    return names


def test_no_ai_or_mcp_module_imports_the_reveal_service():
    """The boundary, enforced structurally rather than by review.

    A tool cannot return a secret it has no way to obtain. ``connector_secrets`` is the ONLY
    module that reads a credential value out of a connector store, so keeping it unreachable
    from ``backend/ai`` and ``backend/mcp`` is what makes the model-facing sweeps above hold
    for tools nobody has written yet.
    """
    offenders = [
        str(path)
        for path in _modules_under("ai", "mcp")
        if any(
            name == "backend.services.connector_secrets"
            or name.endswith(".connector_secrets")
            or name == "connector_secrets"
            for name in _imports_of(path)
        )
    ]
    assert not offenders, (
        "backend/ai or backend/mcp imports services.connector_secrets — the reveal path is "
        "operator-only and must stay unreachable from a model-facing module: "
        + ", ".join(offenders)
    )


def test_reveal_is_not_registered_as_a_tool():
    """No ``@tool`` may wrap the reveal — not under this name, not under a friendlier one."""
    import backend.ai.tools  # noqa: F401 — importing the package registers every tool
    from backend.ai.tools.registry import registered_tool_names

    names = set(registered_tool_names())
    assert "reveal_connector_secret" not in names
    assert "reveal_secret" not in names
    suspicious = sorted(n for n in names if "reveal" in n or "secret" in n)
    assert not suspicious, f"a tool named for revealing secrets exists: {suspicious}"
