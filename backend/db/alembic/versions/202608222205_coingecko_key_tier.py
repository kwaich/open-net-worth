"""Rename `market_data_config.coingecko_demo_api_key` -> `coingecko_api_key` + add
`coingecko_api_key_tier` (data-only rewrite of one `secret_config` row's JSON payload — no
schema change, `secret_config` already stores an opaque `payload` JSON column).

## Why

CoinGecko's Pro tier was explicitly requested (a paid key needs a DIFFERENT host,
`pro-api.coingecko.com` instead of `api.coingecko.com`, and a DIFFERENT header,
`x-cg-pro-api-key` instead of `x-cg-demo-api-key`) but the store only ever had a Demo-tier
field. Renaming it generic (`coingecko_api_key`) and adding an explicit tier alongside it
(`coingecko_api_key_tier`, `"demo"` | `"pro"`) is the field-level half of that fix — see
`backend/services/coingecko_client.py` for the host/header selection and
`frontend/.../CoinGeckoSection.tsx` for the tier picker.

Per this codebase's "no backward-compat shims" rule, this is a real data migration, not a
dual-read fallback in `MarketDataConfigStore`: any EXISTING saved Demo key is carried
forward verbatim under the new field name, stamped `coingecko_api_key_tier="demo"` (every
key saved before this revision was necessarily a Demo key — the Pro option did not exist
yet), so no operator's saved credential is silently dropped. A row that doesn't exist yet
(nothing ever saved) is a correct no-op — a fresh instance seeds straight from
`_seed_from_env()` on first read.

Revision ID: 0048
Revises: 0047
Create Date: 2026-08-22
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "202608222205_coingecko_key_tier"
down_revision = "202608221703_drop_mcp_pending_calls"
branch_labels = None
depends_on = None

_NAMESPACE = "market_data_config"

# Frozen local reference to `secret_config`'s shape (namespace TEXT PK, payload JSON) —
# a migration must not depend on the live `backend.db.tables` module, which is free to
# change shape after this revision (mirrors revision 0031's own `_mcp_pending_calls`
# precedent, see that revision's comment for the full reasoning).
_secret_config = sa.table(
    "secret_config",
    sa.column("namespace", sa.Text),
    sa.column("payload", sa.JSON),
)


def upgrade() -> None:
    conn = op.get_bind()
    row = conn.execute(
        sa.select(_secret_config.c.payload).where(_secret_config.c.namespace == _NAMESPACE)
    ).first()
    if row is None:
        return  # nothing ever saved — a fresh instance seeds from env on first read
    payload = dict(row[0] or {})
    if "coingecko_demo_api_key" not in payload:
        return  # already migrated, or a row that never held the old field
    payload["coingecko_api_key"] = payload.pop("coingecko_demo_api_key")
    payload.setdefault("coingecko_api_key_tier", "demo")
    conn.execute(
        sa.update(_secret_config)
        .where(_secret_config.c.namespace == _NAMESPACE)
        .values(payload=payload)
    )


def downgrade() -> None:
    conn = op.get_bind()
    row = conn.execute(
        sa.select(_secret_config.c.payload).where(_secret_config.c.namespace == _NAMESPACE)
    ).first()
    if row is None:
        return
    payload = dict(row[0] or {})
    if "coingecko_api_key" not in payload:
        return
    payload["coingecko_demo_api_key"] = payload.pop("coingecko_api_key")
    payload.pop("coingecko_api_key_tier", None)
    conn.execute(
        sa.update(_secret_config)
        .where(_secret_config.c.namespace == _NAMESPACE)
        .values(payload=payload)
    )
