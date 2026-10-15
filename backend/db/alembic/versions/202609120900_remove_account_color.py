"""Remove the obsolete account colour payload field.

Account colour was never a promoted SQL column; it existed only inside each account's
round-trip JSON payload. The migration therefore rewrites that payload rather than issuing
DDL. Downgrade cannot recover an intentionally discarded user preference.

Revision ID: 202609120900_remove_account_color
Revises: 202609111200_monthly_retirement_spending
Create Date: 2026-09-12
"""

from __future__ import annotations

import json

import sqlalchemy as sa
from alembic import op

revision = "202609120900_remove_account_color"
down_revision = "202609111200_monthly_retirement_spending"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    rows = conn.execute(sa.text("SELECT id, payload FROM accounts")).fetchall()
    for row_id, raw_payload in rows:
        payload = (
            json.loads(raw_payload) if isinstance(raw_payload, str) else dict(raw_payload or {})
        )
        if "color" not in payload:
            continue
        payload.pop("color")
        conn.execute(
            sa.text("UPDATE accounts SET payload = :payload WHERE id = :id"),
            {"id": row_id, "payload": json.dumps(payload)},
        )


def downgrade() -> None:
    pass
