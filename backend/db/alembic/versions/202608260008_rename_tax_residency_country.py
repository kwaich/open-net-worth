"""Rename the ``tax_residency_country`` profile key to ``country_of_residence``.

Despite the name it was never a tax field in practice — Settings has always rendered it as
"Country of residence" (``ProfileSection.tsx``), and its live readers are Lucius's user
context (``ai/prompts.py``) and news localization (``monitoring/intel_agent.py``), both of
which outlived the tax feature removed in revision 0058. That revision's own note deferred
this exact rename ("Renaming it is its own change") — this is that follow-up.

Forward-only in the sense that there is no data loss to guard against (a straight key
rename), but ``downgrade()`` still reverses it. ``profile`` is a per-book singleton with the
whole profile in one JSON ``payload`` column, so this is a payload rewrite, not a schema
change — same shape as 0058: a payload that cannot be parsed is skipped rather than failing
the boot, since one unreadable profile must not block every other book's migration.

Revision ID: 0059
Revises: 0058
Create Date: 2026-08-25
"""

from __future__ import annotations

import json
import logging

from alembic import op

log = logging.getLogger("alembic.rename_tax_residency_country")

revision = "202608260008_rename_tax_residency_country"
down_revision = "202608260007_drop_profile_tax_config"
branch_labels = None
depends_on = None

OLD_KEY = "tax_residency_country"
NEW_KEY = "country_of_residence"


def _rename(conn, *, old: str, new: str) -> None:
    rows = conn.exec_driver_sql("SELECT book_id, payload FROM profile").fetchall()
    touched = 0
    for book_id, raw in rows:
        try:
            payload = json.loads(raw) if isinstance(raw, str) else raw
        except Exception:  # noqa: BLE001 — one unreadable profile must not stop the rest
            log.warning("profile payload for book %s is unreadable; skipped", book_id)
            continue
        if not isinstance(payload, dict) or old not in payload:
            continue
        payload[new] = payload.pop(old)
        conn.exec_driver_sql(
            "UPDATE profile SET payload = ? WHERE book_id = ?",
            (json.dumps(payload), book_id),
        )
        touched += 1
    if touched:
        log.info("renamed %s -> %s in %d profile payload(s)", old, new, touched)


def upgrade() -> None:
    _rename(op.get_bind(), old=OLD_KEY, new=NEW_KEY)


def downgrade() -> None:
    _rename(op.get_bind(), old=NEW_KEY, new=OLD_KEY)
