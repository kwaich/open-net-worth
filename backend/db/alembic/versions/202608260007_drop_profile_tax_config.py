"""Drop the ``tax_config`` key from every profile payload.

The tax feature was removed outright on 2026-08-25 — the tab, the REST routes, the
services (``tax``/``tax_jurisdictions``/``tlh``), the AI tools, and the shipped
jurisdiction rulesets all went with it.

``profile.tax_config`` (the flat-rate tax model: short/long-term, dividend, interest and
income rate percentages, plus ``jurisdiction``/``rate_source``) was read by
``services/tax.py`` and nothing else. With that module gone the key is unreadable data
sitting in every profile payload, so it is removed rather than left to rot.

Deliberately NOT touched here: ``tax_residency_country``. Despite the name it is not a tax
field in practice — ``ai/prompts.py`` puts it in Lucius's user context and
``monitoring/intel_agent.py`` uses it to localize news, both of which outlive the tax
feature. Renaming it is its own change.

Forward-only. ``profile`` is a per-book singleton with the whole profile in one JSON
``payload`` column, so this is a payload rewrite, not a schema change. A payload that
cannot be parsed is skipped rather than failing the boot — a profile is user data, and one
unreadable row must not block every other book's migration.

Revision ID: 0058
Revises: 0057
Create Date: 2026-08-25
"""

from __future__ import annotations

import json
import logging

from alembic import op

log = logging.getLogger("alembic.drop_profile_tax_config")

revision = "202608260007_drop_profile_tax_config"
down_revision = "202608250013_session_message_chronological_ids"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    rows = conn.exec_driver_sql("SELECT book_id, payload FROM profile").fetchall()
    touched = 0
    for book_id, raw in rows:
        try:
            payload = json.loads(raw) if isinstance(raw, str) else raw
        except Exception:  # noqa: BLE001 — one unreadable profile must not stop the rest
            log.warning("profile payload for book %s is unreadable; skipped", book_id)
            continue
        if not isinstance(payload, dict) or "tax_config" not in payload:
            continue
        del payload["tax_config"]
        conn.exec_driver_sql(
            "UPDATE profile SET payload = ? WHERE book_id = ?",
            (json.dumps(payload), book_id),
        )
        touched += 1
    if touched:
        log.info("dropped tax_config from %d profile payload(s)", touched)


def downgrade() -> None:
    """Nothing to reconstruct — the service those rates meant anything to no longer exists."""
