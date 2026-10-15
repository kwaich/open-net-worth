"""Rotate the instance API token once: older builds logged it at startup.

Builds before this one wrote ``Temporary API token: <token>`` to ``app.log`` whenever no
password was configured, and ``app.log`` is readable through the LogViewer and the
``get_recent_logs`` tool. A token already sitting in a log file stays valid forever unless it
is replaced, so this migration replaces it. The token is generated on first access when the
row is absent, so a brand-new database needs nothing.

Downgrade is a no-op: the old value is deliberately unrecoverable.

Revision ID: 202610041200_rotate_api_token_once
Revises: 202610031200_envelope_groups_not_category_groups
Create Date: 2026-10-04
"""

from __future__ import annotations

import json
import secrets

import sqlalchemy as sa
from alembic import op

revision = "202610041200_rotate_api_token_once"
down_revision = "202610031200_envelope_groups_not_category_groups"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    if "secret_config" not in sa.inspect(conn).get_table_names():
        return
    raw = conn.execute(
        sa.text("SELECT payload FROM secret_config WHERE namespace = 'auth'")
    ).scalar()
    if raw is None:
        return
    try:
        payload = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError) as exc:
        raise RuntimeError("auth payload is unreadable; migration refused") from exc
    if not isinstance(payload, dict) or not payload.get("api_token"):
        return
    payload["api_token"] = secrets.token_urlsafe(32)
    conn.execute(
        sa.text("UPDATE secret_config SET payload = :p WHERE namespace = 'auth'"),
        {"p": json.dumps(payload)},
    )


def downgrade() -> None:
    pass
