"""A real ``connectors`` table: one row per connector, secrets in ``connector_secrets``.

Until now a connector was assembled on every call from 15 differently-shaped ``secret_config``
namespaces, three ``runtime_state`` health namespaces and the ``mcp_clients`` table (RFC
``connectors-table``). This creates ``connectors`` (settings + app-written state, nothing
sensitive) and ``connector_secrets`` (one locked-down row per connector), moves every existing
row onto them, VERIFIES the round trip, and deletes the old namespaces.

Mapping (frozen here — a revision never imports live app code, so it cannot drift with it):

  * ``email_ingest`` + ``channels_config.email``      -> ``email``  (one identity: the IMAP user,
    one password; a differing SMTP login/password/recipient is dropped; ``enabled`` is the AND
    of the legs that were configured)
  * ``channels_config.webhook``                       -> ``webhook`` (url and secret are secrets)
  * ``telegram_config`` + ``connector_sessions``      -> ``telegram`` (chat map -> secret ``sessions``)
  * the nine ``<provider>_config`` aggregators        -> ``<provider>``
  * ``search_config.providers.<id>``                  -> ``web_search:<id>``
  * ``market_data_config`` + health namespaces        -> ``coingecko`` + ``yahoo_finance``
    (always created; their circuit and quota rows become ``state``. The price-source telemetry
    log, ``runtime_state.market_data_observability``, is large and written on every call, so it
    stays where it is — untouched)
  * ``mcp_clients`` rows                              -> ``mcp:<id>`` (url/command/args/env/auth
    header/token/OAuth secrets -> secrets: they routinely embed credentials)
  * ``slack/discord/whatsapp_inbound/gocardless/pluggy_config`` -> deleted (orphans)

``auth`` and ``model_config`` stay in ``secret_config``. A source payload that is not a JSON
object RAISES (credentials must not vanish silently); the boot wrapper then restores its
pre-migrate backup and refuses to start. ``last_error`` strings are redacted on the way in,
exactly as ``ConnectorStore.merge_state`` does, because ``connectors`` is readable by Lucius's
SQL while ``secret_config`` was not.

``mcp_clients`` is dropped here once its rows are verified in ``connectors`` (``McpClientStore``
is now a facade over ``ConnectorStore``).

``downgrade()`` raises: this redesign guarantees upgrade-in-place and nothing else.

Revision ID: 202610101200_connectors_table
Revises: 202610071700_session_transcript_rev
Create Date: 2026-10-10
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Iterator, Optional

import sqlalchemy as sa
from alembic import op

revision = "202610101200_connectors_table"
down_revision = "202610071700_session_transcript_rev"
branch_labels = None
depends_on = None
# Read by the tests (tests/db/conftest.py::reversible_tip): a chain walk never downgrades across it.
irreversible = True

log = logging.getLogger("alembic.connectors_table")

_connectors = sa.table(
    "connectors",
    sa.column("id", sa.Text),
    sa.column("kind", sa.Text),
    sa.column("name", sa.Text),
    sa.column("enabled", sa.Boolean),
    sa.column("config", sa.JSON),
    sa.column("state", sa.JSON),
    sa.column("created_at", sa.Text),
    sa.column("updated_at", sa.Text),
)
_connector_secrets = sa.table(
    "connector_secrets",
    sa.column("connector_id", sa.Text),
    sa.column("secrets", sa.JSON),
    sa.column("updated_at", sa.Text),
)
_secret_config = sa.table(
    "secret_config", sa.column("namespace", sa.Text), sa.column("payload", sa.JSON)
)
_runtime_state = sa.table(
    "runtime_state", sa.column("namespace", sa.Text), sa.column("payload", sa.JSON)
)
_mcp_clients = sa.table(
    "mcp_clients",
    sa.column("seq", sa.Integer),
    sa.column("id", sa.Text),
    sa.column("payload", sa.JSON),
)

# ── frozen mapping tables ───────────────────────────────────────────────────────────────────
# provider key -> (display name, config fields, secret fields, state fields)
_AGGREGATORS: dict[str, tuple[str, tuple, tuple, tuple]] = {
    "simplefin": (
        "SimpleFIN",
        (),
        ("access_url",),
        ("last_synced_at", "last_status", "last_error"),
    ),
    "plaid": (
        "Plaid",
        ("client_id", "environment"),
        ("secret", "access_token", "item_id"),
        ("last_synced_at", "last_status", "last_error"),
    ),
    "lunch_money": (
        "Lunch Money",
        (),
        ("access_token",),
        ("last_synced_at", "last_status", "last_error"),
    ),
    "lunch_flow": (
        "Lunch Flow",
        ("client_id", "email"),
        ("client_secret", "access_token", "refresh_token"),
        (
            "token_expires_at",
            "user_id",
            "last_synced_at",
            "last_status",
            "last_error",
            "last_caution",
            "accounts_meta",
        ),
    ),
    "ynab": (
        "YNAB",
        ("budget_id",),
        ("access_token",),
        ("budget_name", "budget_currency", "last_synced_at", "last_status", "last_error"),
    ),
    "enable_banking": (
        "Enable Banking",
        ("application_id", "aspsp", "country"),
        ("private_key", "session_id"),
        ("last_synced_at", "last_status", "last_error"),
    ),
    "yodlee": (
        "Yodlee",
        ("client_id", "api_endpoint", "username"),
        ("secret", "password", "cobrand_session_token", "user_session_token"),
        ("last_synced_at", "last_status", "last_error"),
    ),
    "alpaca": (
        "Alpaca",
        ("environment",),
        ("api_key_id", "secret_key"),
        ("last_synced_at", "last_status", "last_error", "cash", "buying_power"),
    ),
    "ibkr": (
        "Interactive Brokers",
        ("flex_query_id", "base_url"),
        ("flex_token",),
        ("last_synced_at", "last_status", "last_error"),
    ),
}

# search provider id -> (label, extra non-secret config fields)
_SEARCH_PROVIDERS: dict[str, tuple[str, tuple]] = {
    "duckduckgo": ("DuckDuckGo", ()),
    "tavily": ("Tavily", ()),
    "perplexity": ("Perplexity (Sonar)", ()),
    "serper": ("Serper", ()),
    "google_pse": ("Google Programmable Search", ("cx",)),
    "firecrawl": ("Firecrawl", ()),
}

_KEY_FREE_SEARCH = frozenset({"duckduckgo"})

_MCP_OAUTH_STATE = (
    "issuer",
    "resource",
    "authorization_endpoint",
    "token_endpoint",
    "revocation_endpoint",
    "client_id",
    "token_endpoint_auth_method",
    "scope",
    "expires_at",
    "obtained_at",
)

_ORPHAN_NAMESPACES = (
    "slack_config",
    "discord_config",
    "whatsapp_inbound_config",
    "gocardless_config",
    "pluggy_config",
)

# ── error redaction (frozen copy of core.redact_error) ──────────────────────────────────────
_USERINFO = re.compile(
    r"(?P<scheme>[A-Za-z][A-Za-z0-9+.\-]*://)"
    r"(?:[^/\s?#]*|[\w.\-%+]+:(?!\d{1,5}(?:[/?#\s]|$))[^\s?#]*)@"
)
_AUTHORIZATION = re.compile(
    r"(?P<head>\bauthorization[\"']?\s*[:=]\s*[\"']?)"
    r"(?:(?:bearer|basic|token)\s+)?[^\s\"',;]+",
    re.IGNORECASE,
)
_BEARER = re.compile(r"\b(?P<scheme>bearer)\s+[A-Za-z0-9._~+/=\-]{6,}", re.IGNORECASE)
_CREDENTIAL_WORD = r"(?:token|key|secret|passw(?:or)?d|pwd|credential|authorization)"
_JSON_PAIR = re.compile(
    r"(?P<name_q>[\"']?)(?P<name>[\w.\-]*" + _CREDENTIAL_WORD + r"[\w.\-]*)(?P=name_q)"
    r"(?P<sep>\s*:\s*)(?P<q>[\"'])(?P<value>[^\"'\n]*)(?P=q)",
    re.IGNORECASE,
)
_SECRET_PAIR = re.compile(
    r"(?<![\w.\-])"
    r"(?P<name>[\w.\-]*(?:token|key|secret|passw(?:or)?d|pwd|access_?url)[\w.\-]*)"
    r"=(?P<value>[^&\s\"'<>)\]]+)",
    re.IGNORECASE,
)
_QUERY_PAIR = re.compile(
    r"(?<=[?&])(?P<name>sig|signature|code|sas|auth|jwt)=(?P<value>[^&\s\"'<>)\]#]+)",
    re.IGNORECASE,
)
_OPAQUE_SEGMENT = re.compile(r"(?<=/)[A-Za-z0-9_\-]{20,}(?=[/?#\s\"')\]]|$)")


def _mask_opaque_segment(match: "re.Match[str]") -> str:
    segment = match.group(0)
    has_letter = any(c.isalpha() for c in segment)
    has_digit = any(c.isdigit() for c in segment)
    return "***" if has_letter and has_digit else segment


def _redact(text: str) -> str:
    text = _USERINFO.sub(lambda m: f"{m.group('scheme')}***@", text)
    text = _AUTHORIZATION.sub(lambda m: f"{m.group('head')}***", text)
    text = _BEARER.sub(lambda m: f"{m.group('scheme')} ***", text)
    text = _JSON_PAIR.sub(
        lambda m: (
            f"{m.group('name_q')}{m.group('name')}{m.group('name_q')}{m.group('sep')}"
            f"{m.group('q')}***{m.group('q')}"
        ),
        text,
    )
    text = _SECRET_PAIR.sub(lambda m: f"{m.group('name')}=***", text)
    text = _QUERY_PAIR.sub(lambda m: f"{m.group('name')}=***", text)
    return _OPAQUE_SEGMENT.sub(_mask_opaque_segment, text)


# ── reading the old rows ────────────────────────────────────────────────────────────────────


def _object(table: str, key: str, value: Any) -> dict:
    if not isinstance(value, dict):
        raise RuntimeError(
            f"connectors migration: {table} {key!r} is not a JSON object "
            f"(got {type(value).__name__}); aborting rather than dropping its credentials"
        )
    return value


def _namespaces(conn, table) -> dict[str, dict]:
    rows = conn.execute(sa.select(table.c.namespace, table.c.payload)).all()
    return {ns: _object(table.name, ns, payload) for ns, payload in rows}


def _blank(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    return isinstance(value, (list, dict)) and not value


def _present(source: dict, keys: tuple) -> dict:
    """The listed keys that are set (``None`` means unset in the new store)."""
    return {k: source[k] for k in keys if source.get(k) is not None}


def _secrets_of(source: dict, keys: tuple) -> dict:
    return {k: source[k] for k in keys if not _blank(source.get(k))}


def _state_of(source: dict, keys: tuple) -> dict:
    state = _present(source, keys)
    if "last_error" in state:
        if isinstance(state["last_error"], str) and state["last_error"].strip():
            state["last_error"] = _redact(state["last_error"])
        else:
            del state["last_error"]
    return state


def _row(cid, kind, name, enabled, config=None, secrets=None, state=None) -> dict:
    return {
        "id": cid,
        "kind": kind,
        "name": name,
        "enabled": bool(enabled),
        "config": config or {},
        "secrets": secrets or {},
        "state": state or {},
    }


# ── building the new rows ───────────────────────────────────────────────────────────────────


def _email(imap: Optional[dict], channels: Optional[dict]) -> Iterator[dict]:
    smtp = (channels or {}).get("email")
    if imap is None and smtp is None:
        return
    imap = imap or {}
    smtp = _object("secret_config", "channels_config.email", smtp or {})

    def text(source: dict, key: str) -> str:
        value = source.get(key)
        return value.strip() if isinstance(value, str) else ""

    imap_ready = bool(
        text(imap, "imap_host") and text(imap, "imap_user") and text(imap, "imap_password")
    )
    smtp_ready = bool(text(smtp, "smtp_host") and text(smtp, "from_addr") and text(smtp, "to_addr"))
    legs = []
    if imap_ready:
        legs.append(bool(imap.get("enabled")))
    if smtp_ready:
        legs.append(bool(smtp.get("enabled")))
    config = {
        "provider": imap.get("provider"),
        "address": text(imap, "imap_user") or text(smtp, "smtp_user") or text(smtp, "from_addr"),
        "imap_host": imap.get("imap_host", ""),
        "imap_port": imap.get("imap_port", 993),
        "imap_folder": imap.get("imap_folder", "INBOX"),
        "lookback_days": imap.get("lookback_days", 7),
        "smtp_host": smtp.get("smtp_host", ""),
        "smtp_port": smtp.get("smtp_port", 587),
        "use_tls": smtp.get("use_tls", True),
    }
    password = (
        imap.get("imap_password") if text(imap, "imap_password") else smtp.get("smtp_password")
    )
    yield _row(
        "email",
        "email",
        "Email",
        bool(legs) and all(legs),
        {k: v for k, v in config.items() if v is not None},
        _secrets_of({"password": password}, ("password",)),
        _state_of(imap, ("last_status", "last_error")),
    )


def _webhook(channels: Optional[dict]) -> Iterator[dict]:
    webhook = (channels or {}).get("webhook")
    if webhook is None:
        return
    webhook = _object("secret_config", "channels_config.webhook", webhook)
    yield _row(
        "webhook",
        "webhook",
        "Webhook",
        webhook.get("enabled"),
        {"secret_header_name": webhook.get("secret_header_name") or "X-Webhook-Secret"},
        _secrets_of(webhook, ("url", "secret")),
    )


def _split_ids(raw: Any) -> list:
    if isinstance(raw, list):
        return raw
    if isinstance(raw, str):
        return [x.strip() for x in raw.split(",") if x.strip()]
    return []


def _telegram(telegram: Optional[dict], sessions: Optional[dict]) -> Iterator[dict]:
    if telegram is None and not sessions:
        return
    telegram = telegram or {}
    config = _present(telegram, ("access_policy", "streaming", "suggestions", "tool_stream"))
    if "allowed_chat_ids" in telegram:
        config["allowed_chat_ids"] = _split_ids(telegram["allowed_chat_ids"])
    secrets = _secrets_of(telegram, ("bot_token",))
    if sessions:
        secrets["sessions"] = sessions
    yield _row("telegram", "telegram", "Telegram", telegram.get("enabled"), config, secrets)


def _aggregators(namespaces: dict) -> Iterator[dict]:
    for key, (label, config_keys, secret_keys, state_keys) in _AGGREGATORS.items():
        payload = namespaces.get(f"{key}_config")
        if payload is None:
            continue
        yield _row(
            key,
            key,
            label,
            payload.get("enabled"),
            _present(payload, config_keys),
            _secrets_of(payload, secret_keys),
            _state_of(payload, state_keys),
        )


def _search_providers(payload: Optional[dict]) -> dict:
    """The per-provider entries of a ``search_config`` payload, whichever shape it is in."""
    if payload is None:
        return {}
    if not isinstance(payload.get("providers"), dict) and (
        "keys" in payload or "provider" in payload
    ):
        keys = payload.get("keys") if isinstance(payload.get("keys"), dict) else {}
        selected = payload.get("provider")
        enabled = bool(payload.get("enabled"))
        entries = {
            pid: {"api_key": str(key).strip(), "enabled": enabled and pid == selected}
            for pid, key in keys.items()
            if pid in _SEARCH_PROVIDERS and str(key or "").strip()
        }
        return {"providers": entries, "default": selected}
    default = (
        payload["active_provider"]
        if "active_provider" in payload
        else payload.get("default_provider")
    )
    providers = payload.get("providers")
    return {"providers": providers if isinstance(providers, dict) else {}, "default": default}


def _web_search(payload: Optional[dict]) -> Iterator[dict]:
    parsed = _search_providers(payload)
    providers = {
        pid: entry
        for pid, entry in parsed.get("providers", {}).items()
        if pid in _SEARCH_PROVIDERS and isinstance(entry, dict)
    }
    # A default naming a provider that has no stored entry still needs its row: the flag lives
    # on the row. Key-free providers were enabled until explicitly switched off.
    default = parsed.get("default")
    if default in _SEARCH_PROVIDERS and default not in providers:
        providers[default] = {"enabled": default in _KEY_FREE_SEARCH}
    for pid, entry in providers.items():
        label, extras = _SEARCH_PROVIDERS[pid]
        config = {"provider": pid, "default": pid == default}
        config.update({k: str(entry[k]) for k in extras if entry.get(k)})
        yield _row(
            f"web_search:{pid}",
            "web_search",
            label,
            entry.get("enabled"),
            config,
            _secrets_of({"api_key": str(entry.get("api_key") or "")}, ("api_key",)),
        )


def _health(namespace: str) -> bool:
    return namespace == "coingecko_quota" or namespace.startswith("market_provider_circuit:")


def _market_data(config_payload: Optional[dict], runtime: dict) -> Iterator[dict]:
    source = config_payload or {}
    tier = str(source.get("coingecko_api_key_tier") or "demo").strip().lower()
    api_key = str(source.get("coingecko_api_key") or "").strip()
    primary = source.get("yahoo_primary_for_major_crypto", True)

    state: dict[str, dict] = {"coingecko": {}, "yahoo_finance": {}}
    if runtime.get("coingecko_quota"):
        state["coingecko"]["quota"] = runtime["coingecko_quota"]
    for namespace, payload in runtime.items():
        circuit = namespace.partition("market_provider_circuit:")[2]
        owner = "coingecko" if circuit.startswith("coingecko") else "yahoo_finance"
        if payload and circuit.startswith(("coingecko", "yahoo")):
            state[owner].setdefault("circuit", {})[circuit] = payload

    yield _row(
        "coingecko",
        "coingecko",
        "CoinGecko",
        True,
        {"tier": tier if tier in ("demo", "pro") else "demo"},
        _secrets_of({"api_key": api_key}, ("api_key",)),
        state["coingecko"],
    )
    yield _row(
        "yahoo_finance",
        "yahoo_finance",
        "Yahoo Finance",
        True,
        {"primary_for_major_crypto": bool(primary)},
        None,
        state["yahoo_finance"],
    )


def _mcp(server_id: str, payload: dict) -> dict:
    oauth = payload.get("oauth") if isinstance(payload.get("oauth"), dict) else {}
    secrets = _secrets_of(payload, ("url", "command", "args", "env", "auth_header", "token")) | {
        f"oauth_{k}": oauth[k]
        for k in ("client_secret", "access_token", "refresh_token")
        if not _blank(oauth.get(k))
    }
    config = {
        "transport": payload.get("transport") or "http",
        "auth_type": payload.get("auth_type") or "none",
        "read_only": payload.get("read_only", True),
        "tool_allowlist": payload.get("tool_allowlist") or [],
        "verification": payload.get("verification") or "unknown",
        **_present(payload, ("publisher", "homepage_url", "repo_url")),
    }
    state = _state_of(
        payload,
        (
            "discovered_tools",
            "server_info_name",
            "server_info_version",
            "server_info_instructions",
            "last_status",
            "last_error",
        ),
    )
    if oauth:
        state["oauth"] = _present(oauth, _MCP_OAUTH_STATE)
    name = payload.get("name")
    return _row(
        f"mcp:{server_id}",
        "mcp",
        name.strip() if isinstance(name, str) and name.strip() else server_id,
        payload.get("enabled"),
        config,
        secrets,
        state,
    )


def _mcp_servers(conn) -> Iterator[dict]:
    for row_id, payload in conn.execute(
        sa.select(_mcp_clients.c.id, _mcp_clients.c.payload).order_by(_mcp_clients.c.seq)
    ):
        payload = _object("mcp_clients", row_id, payload)
        yield _mcp(str(payload.get("id") or row_id), payload)


def _runtime_health(conn) -> dict[str, dict]:
    """The market-data health namespaces. They are regenerated diagnostics, not credentials, so
    an unreadable one is dropped rather than blocking the boot."""
    wanted = {}
    for ns, payload in conn.execute(
        sa.select(_runtime_state.c.namespace, _runtime_state.c.payload)
    ):
        if not _health(ns):
            continue
        if isinstance(payload, dict):
            wanted[ns] = payload
        else:
            log.warning("runtime_state %r is not a JSON object; dropped", ns)
    return wanted


# ── write + verify ──────────────────────────────────────────────────────────────────────────


def _insert(conn, rows: list[dict]) -> None:
    """Insert the rows in order, one microsecond apart: ``connectors`` lists oldest first, so
    each row's ``created_at`` carries its position — an MCP server keeps the place its old
    ``seq`` gave it."""
    start = datetime.now(timezone.utc)
    for i, r in enumerate(rows):
        now = (start + timedelta(microseconds=i)).isoformat()
        conn.execute(
            sa.insert(_connectors).values(
                id=r["id"],
                kind=r["kind"],
                name=r["name"],
                enabled=r["enabled"],
                config=r["config"],
                state=r["state"],
                created_at=now,
                updated_at=now,
            )
        )
        if r["secrets"]:
            conn.execute(
                sa.insert(_connector_secrets).values(
                    connector_id=r["id"], secrets=r["secrets"], updated_at=now
                )
            )


def _verify(conn, rows: list[dict]) -> None:
    """Re-read everything just written and compare it to what was built; any difference raises."""
    stored = {r.id: r._mapping for r in conn.execute(sa.select(*_connectors.c)).all()}
    stored_secrets = {
        cid: secrets
        for cid, secrets in conn.execute(
            sa.select(_connector_secrets.c.connector_id, _connector_secrets.c.secrets)
        )
    }
    expected = {r["id"]: r for r in rows}
    if set(stored) != set(expected) or set(stored_secrets) != {
        i for i, r in expected.items() if r["secrets"]
    }:
        raise RuntimeError("connectors migration: stored connector set differs from the source")
    for cid, want in expected.items():
        got = stored[cid]
        mismatched = [
            field for field in ("kind", "name", "config", "state") if got[field] != want[field]
        ]
        if bool(got["enabled"]) != want["enabled"]:
            mismatched.append("enabled")
        if stored_secrets.get(cid, {}) != want["secrets"]:
            mismatched.append("secrets")
        if mismatched:
            raise RuntimeError(
                f"connectors migration: round trip of {cid!r} differs in {mismatched}; aborting"
            )


def _create_tables() -> None:
    op.create_table(
        "connectors",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("kind", sa.Text, nullable=False),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("config", sa.JSON, nullable=False),
        sa.Column("state", sa.JSON, nullable=False),
        sa.Column("created_at", sa.Text, nullable=False),
        sa.Column("updated_at", sa.Text, nullable=False),
    )
    op.create_index("ix_connectors_kind", "connectors", ["kind"])
    op.create_table(
        "connector_secrets",
        sa.Column(
            "connector_id",
            sa.Text,
            sa.ForeignKey("connectors.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("secrets", sa.JSON, nullable=False),
        sa.Column("updated_at", sa.Text, nullable=False),
    )


def upgrade() -> None:
    conn = op.get_bind()
    namespaces = _namespaces(conn, _secret_config)
    runtime = _runtime_health(conn)

    channels = namespaces.get("channels_config")
    rows = [
        *_email(namespaces.get("email_ingest"), channels),
        *_webhook(channels),
        *_telegram(namespaces.get("telegram_config"), namespaces.get("connector_sessions")),
        *_aggregators(namespaces),
        *_web_search(namespaces.get("search_config")),
        *_market_data(namespaces.get("market_data_config"), runtime),
        *_mcp_servers(conn),
    ]
    _create_tables()
    _insert(conn, rows)
    _verify(conn, rows)

    migrated = {
        "email_ingest",
        "channels_config",
        "telegram_config",
        "connector_sessions",
        "search_config",
        "market_data_config",
        *(f"{key}_config" for key in _AGGREGATORS),
        *_ORPHAN_NAMESPACES,
    }
    conn.execute(sa.delete(_secret_config).where(_secret_config.c.namespace.in_(sorted(migrated))))
    stale_health = [
        ns for (ns,) in conn.execute(sa.select(_runtime_state.c.namespace)) if _health(ns)
    ]
    if stale_health:
        conn.execute(sa.delete(_runtime_state).where(_runtime_state.c.namespace.in_(stale_health)))
    op.drop_table("mcp_clients")
    log.info("connectors table: migrated %d connector(s)", len(rows))


def downgrade() -> None:
    raise NotImplementedError(
        "the connectors table has no downgrade: it replaces fifteen settings rows and the "
        "mcp_clients table in place. Restore the pre-migration backup instead."
    )
