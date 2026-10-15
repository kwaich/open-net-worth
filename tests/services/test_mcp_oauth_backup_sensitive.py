"""A refresh token stored in `connector_secrets` (an MCP server's `oauth_*` fields) inherits the
SAME protections as every other `SECRET_TABLES` row (RFC 2026-08-14-mcp-oauth §4.4's storage
claim, carried onto the connectors tables by RFC connectors-table) — verified empirically
rather than trusted, exactly the way `tests/services/test_backup_restore_db_aware.py` proves
`NON_RESTORABLE_TABLES` for the rest of the money-adjacent set. Same fixture shape: DATA_DIR
== tmp_path with a real, migrated onw.db living inside it (via db_isolated), so `run_backup`
actually walks/zips the database and `write_sanitised_copy` actually rewrites it.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from backend.core import portable, sensitive
from backend.db import portable_bridge
from backend.db.engine import dispose_engine
from backend.db.mcp_client_store import McpClientStore
from backend.models.mcp_client import McpOAuth, McpServerConfig
from backend.services import backup as backup_service

_NOW = datetime.now(timezone.utc).replace(hour=3, minute=30, second=0, microsecond=0)

_REFRESH_SENTINEL = "SENTINEL-refresh-token-9f3c2a"
_ACCESS_SENTINEL = "SENTINEL-access-token-4b1e77"
_SECRET_SENTINEL = "SENTINEL-client-secret-0d88aa"


@pytest.fixture
def isolated(tmp_path, monkeypatch, db_isolated):
    data_dir = tmp_path
    backups_dir = data_dir / "backups"
    backups_dir.mkdir()
    monkeypatch.setattr(backup_service, "DATA_DIR", data_dir)
    monkeypatch.setattr(backup_service, "BACKUPS_DIR", backups_dir)
    monkeypatch.setattr(backup_service, "_EXCLUDE_DIR_NAMES", {"backups", "checkpoints"})
    return data_dir, backups_dir


def _seed_oauth_server() -> McpClientStore:
    store = McpClientStore()
    store.create(
        McpServerConfig(
            id="mercury",
            name="Mercury",
            transport="http",
            url="https://mcp.mercury.com/mcp",
            auth_type="oauth",
        )
    )
    store.set_oauth(
        "mercury",
        McpOAuth(
            issuer="https://mcp.mercury.com/",
            resource="https://mcp.mercury.com/mcp",
            authorization_endpoint="https://mcp.mercury.com/authorize",
            token_endpoint="https://mcp.mercury.com/token",
            revocation_endpoint="https://mcp.mercury.com/revoke",
            client_id="dcr-client-1",
            client_secret=_SECRET_SENTINEL,
            token_endpoint_auth_method="client_secret_basic",
            access_token=_ACCESS_SENTINEL,
            refresh_token=_REFRESH_SENTINEL,
            expires_at=1_900_000_000.0,
            obtained_at=1_899_996_400.0,
        ),
    )
    return store


def _restore_and_reconnect(name: str) -> dict:
    result = backup_service.restore_backup(name)
    dispose_engine()
    return result


# ── 1. A sanitised download DELETEs the row, not just the token field ──────────


def test_sanitised_download_strips_the_oauth_bearing_connector_secrets_table(isolated, tmp_path):
    _seed_oauth_server()
    backup = backup_service.run_backup(now=_NOW)
    assert backup["ok"] is True, backup

    out = tmp_path / "download.zip"
    result = backup_service.write_sanitised_copy(backup["name"], out)
    assert result["ok"] is True, result

    import sqlite3
    import zipfile

    with zipfile.ZipFile(out) as zf:
        db_member = next(n for n in zf.namelist() if n.endswith(".db"))
        raw = zf.read(db_member)
    work = tmp_path / "check.db"
    work.write_bytes(raw)
    conn = sqlite3.connect(work)
    try:
        secret_rows = conn.execute("SELECT secrets FROM connector_secrets").fetchall()
        server = conn.execute("SELECT id FROM connectors WHERE id = 'mcp:mercury'").fetchall()
    finally:
        conn.close()
    assert secret_rows == []  # the ROW is gone, not merely the secret fields inside it
    assert server == [("mcp:mercury",)]  # the non-secret half still ships

    # Belt-and-braces: the sentinel values never appear anywhere in the shipped bytes at all
    # (proves secure_delete + VACUUM actually scrubbed the freed page, not just hid the row
    # from an ordinary SELECT).
    assert _REFRESH_SENTINEL.encode() not in raw
    assert _ACCESS_SENTINEL.encode() not in raw
    assert _SECRET_SENTINEL.encode() not in raw


# ── 2. A restore never un-revokes a rotated/cleared oauth token ────────────────


def test_restore_never_reverts_a_cleared_oauth_token(isolated):
    store = _seed_oauth_server()
    backup = backup_service.run_backup(now=_NOW)
    assert backup["ok"] is True, backup

    # The owner disconnects (or a background refresh discards a dead registration) AFTER
    # the backup was taken.
    store.clear_oauth("mercury")
    assert store.find("mercury").oauth is None

    result = _restore_and_reconnect(backup["name"])
    assert result["ok"] is True, result

    # Pre-fix-class bug: a restore swaps onw.db wholesale, resurrecting the token that
    # existed at backup time. connector_secrets is in NON_RESTORABLE_TABLES, so it must not.
    assert McpClientStore().find("mercury").oauth is None


def test_restore_never_reverts_a_rotated_refresh_token(isolated):
    store = _seed_oauth_server()
    backup = backup_service.run_backup(now=_NOW)
    assert backup["ok"] is True, backup

    rotated = store.find("mercury").oauth.model_copy(
        update={"refresh_token": "rotated-refresh-token"}
    )
    store.set_oauth("mercury", rotated)

    result = _restore_and_reconnect(backup["name"])
    assert result["ok"] is True, result

    assert McpClientStore().find("mercury").oauth.refresh_token == "rotated-refresh-token"


# ── 3. The table-level policy sets actually cover the connectors tables ────────


def test_connector_secrets_is_declared_secret_and_non_restorable():
    assert "connector_secrets" in sensitive.SECRET_TABLES
    assert "connector_secrets" in sensitive.NON_RESTORABLE_TABLES
    assert "connectors" in sensitive.NON_RESTORABLE_TABLES
    assert "connectors" not in sensitive.SECRET_TABLES  # readable by Lucius's SQL by design


def test_mcp_clients_table_no_longer_exists():
    from backend.db.tables import metadata

    assert "mcp_clients" not in metadata.tables
    assert "mcp_clients" not in sensitive.SECRET_TABLES
    assert "mcp_clients" not in sensitive.NON_RESTORABLE_TABLES


# ── 4. Absent from /api/data/export (structural — never wired into the portable allowlist) ──


def test_mcp_servers_are_not_reachable_through_export():
    from backend.core.paths import MCP_CLIENTS_JSON

    assert MCP_CLIENTS_JSON not in portable.PORTABLE_FILES
    assert not {"mcp_clients", "connectors", "connector_secrets"} & set(portable_bridge.BRIDGED)
