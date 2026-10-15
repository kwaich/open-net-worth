"""Remove category-level tax metadata from every category payload.

Taxability remains a transaction-level fact. Categories no longer expose or store a
``taxable_default`` preference, so the payload key is discarded rather than left as
unreadable data. Downgrade cannot reconstruct an intentionally removed preference.

Revision ID: 202609211200_remove_category_taxable_default
Revises: 202609201200_price_movement_notes
Create Date: 2026-09-21
"""

from __future__ import annotations

import json

import sqlalchemy as sa
from alembic import op

revision = "202609211200_remove_category_taxable_default"
down_revision = "202609201200_price_movement_notes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    rows = conn.execute(sa.text("SELECT id, payload FROM categories")).fetchall()
    for category_id, raw_payload in rows:
        payload = (
            json.loads(raw_payload) if isinstance(raw_payload, str) else dict(raw_payload or {})
        )
        if "taxable_default" not in payload:
            continue
        payload.pop("taxable_default")
        conn.execute(
            sa.text("UPDATE categories SET payload = :payload WHERE id = :id"),
            {"id": category_id, "payload": json.dumps(payload)},
        )


def downgrade() -> None:
    pass
