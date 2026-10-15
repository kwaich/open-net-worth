"""McpOAuth on the connectors tables — the RFC 2026-08-14-mcp-oauth §4.4 storage claims, pinned
rather than trusted:

  1. The OAuth block is split across the connectors tables (RFC connectors-table): the three
     secrets (``client_secret``/``access_token``/``refresh_token``) ride ``connector_secrets``
     as ``oauth_*``, the rest of the block is ``connectors.state["oauth"]`` — so the readable
     ``connectors`` row never holds a credential — and ``McpClientStore.find`` reassembles the
     exact ``McpOAuth``.
  2. ``public()``/``public_one()`` never leak an OAuth secret value anywhere in the returned
     shape, checked property-style (walk every value in the whole structure) rather than
     against a fixed field list, so a future field addition can't leak by omission silently.

Both were stated as design claims in the RFC; this file is the evidence, not the argument.
"""

from __future__ import annotations

import json

from backend.db.connector_store import ConnectorStore
from backend.db.mcp_client_store import McpClientStore
from backend.models.mcp_client import McpOAuth, McpServerConfig


def _cfg(**over) -> McpServerConfig:
    base = dict(id="mercury", name="Mercury", transport="http", url="https://mcp.mercury.com/mcp")
    base.update(over)
    return McpServerConfig(**base)


def _oauth(**over) -> McpOAuth:
    base = dict(
        issuer="https://mcp.mercury.com/",
        resource="https://mcp.mercury.com/mcp",
        authorization_endpoint="https://mcp.mercury.com/authorize",
        token_endpoint="https://mcp.mercury.com/token",
        revocation_endpoint="https://mcp.mercury.com/revoke",
        client_id="dcr-minted-client-id",
        client_secret="dcr-minted-client-secret",
        token_endpoint_auth_method="client_secret_basic",
        scope="mcp:tools",
        access_token="live-access-token-value",
        refresh_token="live-refresh-token-value",
        expires_at=1_800_000_000.0,
        obtained_at=1_799_996_400.0,
    )
    base.update(over)
    return McpOAuth(**base)


def test_oauth_round_trips_through_the_connectors_tables(db_isolated):
    """A config carrying a full oauth block, written and read through a real (migrated, not
    mocked) engine, comes back exactly — secrets from ``connector_secrets``, the rest from
    ``connectors.state``."""
    store = McpClientStore()
    store.create(_cfg(auth_type="oauth"))
    store.set_oauth("mercury", _oauth())

    fresh = store.find("mercury")
    assert fresh.oauth == _oauth()


def test_oauth_secrets_live_only_in_connector_secrets(db_isolated):
    """The readable ``connectors`` row (a model may SELECT it) carries the non-secret block and
    nothing else of it; every credential is in ``connector_secrets``."""
    store = McpClientStore()
    store.create(_cfg(auth_type="oauth"))
    store.set_oauth("mercury", _oauth())

    row = ConnectorStore().get("mcp:mercury")
    assert row["state"]["oauth"]["issuer"] == "https://mcp.mercury.com/"
    assert row["state"]["oauth"]["client_id"] == "dcr-minted-client-id"
    readable = json.dumps(row)
    for secret in (
        "dcr-minted-client-secret",
        "live-access-token-value",
        "live-refresh-token-value",
    ):
        assert secret not in readable
    assert ConnectorStore().secrets("mcp:mercury") == {
        "url": "https://mcp.mercury.com/mcp",
        "auth_header": "Authorization",
        "oauth_client_secret": "dcr-minted-client-secret",
        "oauth_access_token": "live-access-token-value",
        "oauth_refresh_token": "live-refresh-token-value",
    }


def test_clear_oauth_removes_the_block_and_its_secrets(db_isolated):
    store = McpClientStore()
    store.create(_cfg(auth_type="oauth"))
    store.set_oauth("mercury", _oauth())

    store.clear_oauth("mercury")

    assert store.find("mercury").oauth is None
    assert "oauth" not in ConnectorStore().get("mcp:mercury")["state"]
    assert not {k for k in ConnectorStore().secrets("mcp:mercury") if k.startswith("oauth_")}


def _walk_values(obj):
    if isinstance(obj, dict):
        for v in obj.values():
            yield from _walk_values(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk_values(v)
    else:
        yield obj


def _walk_keys(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield k
            yield from _walk_keys(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk_keys(v)


def test_public_shape_never_contains_an_oauth_secret_value_anywhere(db_isolated):
    """Property-style: seed unique sentinel strings into every SECRET McpOAuth field, then
    scan every value in the ENTIRE public() / public_one() structure (recursively — not just
    the top level) for those sentinels. A field renamed, nested, or duplicated by some future
    change would still be caught, unlike a check pinned to today's known key names."""
    store = McpClientStore()
    store.create(_cfg(auth_type="oauth"))
    secret_client_secret = "SENTINEL-client-secret-3f9a"
    secret_access = "SENTINEL-access-token-7c1e"
    secret_refresh = "SENTINEL-refresh-token-b0d2"
    store.set_oauth(
        "mercury",
        _oauth(
            client_secret=secret_client_secret,
            access_token=secret_access,
            refresh_token=secret_refresh,
        ),
    )

    for pub in (store.public_one("mercury"), store.public()[0]):
        values = list(_walk_values(pub))
        for sentinel in (secret_client_secret, secret_access, secret_refresh):
            assert sentinel not in values, (sentinel, pub)
        keys = set(_walk_keys(pub))
        assert "access_token" not in keys
        assert "refresh_token" not in keys
        assert "client_secret" not in keys
        assert "oauth" in pub and isinstance(pub["oauth"], dict)  # the derived summary IS present


def test_update_refuses_to_patch_oauth_even_via_direct_engine(db_isolated):
    """Belt-and-braces against the DB-level surface specifically (test_mcp_client.py already
    covers this against the store's public methods): update() must ignore an ``oauth`` key in
    the changes dict even when handed a value shaped exactly like a real McpOAuth dump."""
    store = McpClientStore()
    store.create(_cfg(auth_type="oauth"))
    store.set_oauth("mercury", _oauth(access_token="original-token"))

    forged = _oauth(access_token="forged-token").model_dump()
    store.update("mercury", {"oauth": forged})

    assert store.find("mercury").oauth.access_token == "original-token"
