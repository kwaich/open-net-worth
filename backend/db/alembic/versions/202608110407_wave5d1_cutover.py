"""wave5d1 cutover: absorb the 15 connector-credential config files + connector_sessions.json
into secret_config namespace rows, and mcp_clients.json into the mcp_clients table (revision
0026), with round-trip verification. Wave 5D1 (JSON->DB, secrets/config).

Mirrors 0025's (wave5c cutover) ``_absorb_one``/``_stamp_cutover`` exactly, sixteen stores:
read each present source's bytes ONCE (hash + parse the SAME bytes), import, EXACT round-trip
verify (``export_fn(conn) == original``) — a diff RAISES, aborting inside Alembic's one
transaction, and the fail-closed ``ensure_migrated`` wrapper then restores the pre-migrate
backup. No cross-file consistency check (no FKs between any of these sixteen, or to anything
else) — absorption order is COSMETIC. No money guard (none of the sixteen carries a
net-worth-bearing amount — ``wave5d1_io.py``'s own docstring).

Every one of the 15 connector-credential configs + connector_sessions.json parses to a JSON
**object** (dict) — each becomes ONE namespace row in ``secret_config``. ``mcp_clients.json``
parses to a flat JSON **list**. A shape mismatch RAISES rather than silently coercing or
dropping data.

On a fresh/self-hosted instance most of these 16 sources are ABSENT (a connector that was
never configured never had its JSON file created — every *ConfigStore's own ``_read_raw``
falls back to env-seeding, never to writing a placeholder file) — an absent source is a
correct no-op, exactly like every other optional store this migration has absorbed.

Only once every present store's import + round-trip verification has succeeded does a
``json_cutovers`` row land per store (source hash included), so ``_finish_cutovers`` renames
each source to ``<name>.migrated-<ts>`` after ``upgrade head`` fully succeeds, ONLY if the
bytes still hash the same.

Revision ID: 0027
Revises: 0026
Create Date: 2026-08-10
"""

import hashlib
import json
from datetime import datetime, timezone

import sqlalchemy as sa
from alembic import op

revision = "202608110407_wave5d1_cutover"
down_revision = "202608110406_wave5d1_schema"
branch_labels = None
depends_on = None


def _absorb_one(conn, store: str, src, shape: type, import_fn, export_fn):
    """Verbatim port of 0025's own helper (see that revision for the full mechanics the
    sparser comments below rely on)."""
    if not src.exists():
        return None
    raw = src.read_bytes()  # ONE read: the hash below and the parse see identical bytes
    original = json.loads(raw.decode("utf-8"))
    if not isinstance(original, shape):
        expected = "a flat JSON list" if shape is list else "a JSON object"
        raise RuntimeError(
            f"wave5d1 cutover: {src} is not {expected} ({shape.__name__} expected at "
            f"the root) — got {type(original).__name__}; aborting rather than silently "
            "dropping data"
        )
    import_fn(conn, original)
    if export_fn(conn) != original:
        raise RuntimeError(
            f"wave5d1 cutover: {store} round-trip verification FAILED — aborting the "
            "whole migration (all sixteen sources are one all-or-nothing unit); the "
            "fail-closed wrapper will restore the pre-migrate backup"
        )
    return raw, original


def _stamp_cutover(conn, store: str, src, raw: bytes, now: str) -> None:
    conn.execute(
        sa.text(
            "INSERT INTO json_cutovers (store, source_path, source_hash, migrated_at, renamed) "
            "VALUES (:s, :p, :h, :t, 0)"
        ),
        {"s": store, "p": str(src), "h": hashlib.sha256(raw).hexdigest(), "t": now},
    )


# Frozen here: the live ``mcp_clients`` Table left ``db/tables.py`` when the connectors table
# replaced it, and a revision must not depend on live app code that moves on.
mcp_clients = sa.Table(
    "mcp_clients",
    sa.MetaData(),
    sa.Column("seq", sa.Integer, nullable=False),
    sa.Column("id", sa.Text, primary_key=True),
    sa.Column("payload", sa.JSON, nullable=False),
)


def upgrade() -> None:
    from backend.db import wave5d1_io as wio
    from backend.db.alembic import versions_evidence_source as evsrc
    from backend.db.tables import secret_config

    conn = op.get_bind()
    now = datetime.now(timezone.utc).isoformat()

    # (store name, source path, shape, import_fn, export_fn) — the 15 namespace-singleton
    # connector configs + connector_sessions, all rows in the ONE secret_config table.
    namespaced_jobs = [
        ("telegram_config", evsrc.TELEGRAM_CONFIG_SOURCE),
        ("slack_config", evsrc.SLACK_CONFIG_SOURCE),
        ("discord_config", evsrc.DISCORD_CONFIG_SOURCE),
        ("whatsapp_inbound_config", evsrc.WHATSAPP_INBOUND_CONFIG_SOURCE),
        ("plaid_config", evsrc.PLAID_CONFIG_SOURCE),
        ("gocardless_config", evsrc.GOCARDLESS_CONFIG_SOURCE),
        ("simplefin_config", evsrc.SIMPLEFIN_CONFIG_SOURCE),
        ("pluggy_config", evsrc.PLUGGY_CONFIG_SOURCE),
        ("enable_banking_config", evsrc.ENABLE_BANKING_CONFIG_SOURCE),
        ("ibkr_config", evsrc.IBKR_CONFIG_SOURCE),
        ("yodlee_config", evsrc.YODLEE_CONFIG_SOURCE),
        ("alpaca_config", evsrc.ALPACA_CONFIG_SOURCE),
        ("search_config", evsrc.SEARCH_CONFIG_SOURCE),
        ("channels_config", evsrc.CHANNELS_CONFIG_SOURCE),
        ("connector_sessions", evsrc.CONNECTOR_SESSIONS_SOURCE),
    ]

    jobs = [
        (
            store,
            src,
            dict,
            lambda c, raw, ns=store: wio.import_namespaced(c, secret_config, ns, raw),
            lambda c, ns=store: wio.export_namespaced(c, secret_config, ns),
        )
        for store, src in namespaced_jobs
    ]
    jobs.append(
        (
            "mcp_clients",
            evsrc.MCP_CLIENTS_SOURCE,
            list,
            lambda c, raw: wio.import_payload_list(c, mcp_clients, raw),
            lambda c: wio.export_payload_list(c, mcp_clients),
        )
    )

    for store, src, shape, import_fn, export_fn in jobs:
        result = _absorb_one(conn, store, src, shape, import_fn, export_fn)
        if result is not None:
            raw, _original = result
            _stamp_cutover(conn, store, src, raw, now)


def downgrade() -> None:
    """No-op: revision 0026 owns the DDL for secret_config/mcp_clients and drops them when
    a caller downgrades past it — automatic data-destructive downgrades are deliberately not
    provided anywhere in this migration family (0010/0021/0023/0025's own precedent: the
    renamed ``<name>.migrated-<ts>`` sources, once ``ensure_migrated()`` has run for real, are
    the manual restore path)."""
