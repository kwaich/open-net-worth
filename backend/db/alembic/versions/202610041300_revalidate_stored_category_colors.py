"""Re-validate stored category and envelope swatch colours.

A colour reaches the UI as a CSS value. ``create_category`` used to store whatever string it was
handed (only ``set_category_color`` hex-validated), so a stored colour could carry markup. Every
write path now accepts only ``#rrggbb``; this sweep resets any already-stored value that is not
one to null (the UI falls back to a palette colour). Idempotent; downgrade is a no-op because the
discarded values were never valid.

Revision ID: 202610041200_revalidate_stored_category_colors
Revises: 202610031200_envelope_groups_not_category_groups
Create Date: 2026-10-04
"""

from __future__ import annotations

import json
import re

import sqlalchemy as sa
from alembic import op

revision = "202610041300_revalidate_stored_category_colors"
down_revision = "202610041200_rotate_api_token_once"
branch_labels = None
depends_on = None

_HEX_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")
_TABLES = ("categories", "envelopes")


def _dict(raw) -> dict:
    return json.loads(raw) if isinstance(raw, str) else dict(raw or {})


def upgrade() -> None:
    conn = op.get_bind()
    for table in _TABLES:
        for row_id, book_id, raw in conn.execute(
            sa.text(f"SELECT id, book_id, payload FROM {table}")
        ).fetchall():
            payload = _dict(raw)
            color = payload.get("color")
            if color in (None, "") or (isinstance(color, str) and _HEX_COLOR.match(color)):
                continue
            payload["color"] = None
            conn.execute(
                sa.text(f"UPDATE {table} SET payload = :p WHERE id = :id AND book_id = :b"),
                {"p": json.dumps(payload), "id": row_id, "b": book_id},
            )


def downgrade() -> None:
    pass
