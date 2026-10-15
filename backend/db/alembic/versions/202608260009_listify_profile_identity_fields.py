"""Convert the profile's scalar identity fields to lists, and rename to plural.

``nationality`` -> ``nationalities``, ``country_of_residence`` -> ``countries_of_residence``,
``employment_type`` -> ``employment_types``. A person can hold dual citizenship, split
residence, or combine employment types — the scalar fields could only ever record one. A
non-empty scalar string becomes a single-item list; empty/None becomes ``[]``.

``tax_residencies`` is a brand-new field (no prior key) and needs no migration — it is purely
additive and defaults to an absent/empty list.

Same shape as 0059: ``profile`` is a per-book singleton with the whole profile in one JSON
``payload`` column, so this is a payload rewrite, not a schema change. A payload that cannot
be parsed is skipped rather than failing the boot.

``downgrade()`` is necessarily lossy for a multi-value list (dual citizenship, split
residence): it keeps only the FIRST entry and drops the rest, since a scalar field cannot
represent more than one value. Documented, not silent.

Revision ID: 0060
Revises: 0059
Create Date: 2026-08-25
"""

from __future__ import annotations

import json
import logging

from alembic import op

log = logging.getLogger("alembic.listify_profile_identity_fields")

revision = "202608260009_listify_profile_identity_fields"
down_revision = "202608260008_rename_tax_residency_country"
branch_labels = None
depends_on = None

RENAMES = (
    ("nationality", "nationalities"),
    ("country_of_residence", "countries_of_residence"),
    ("employment_type", "employment_types"),
)


def _scalar_to_list(val: object) -> list[str]:
    if isinstance(val, list):
        return [v for v in val if isinstance(v, str) and v]
    if isinstance(val, str) and val:
        return [val]
    return []


def _list_to_scalar(val: object) -> str | None:
    if isinstance(val, list):
        for v in val:
            if isinstance(v, str) and v:
                return v
        return None
    if isinstance(val, str):
        return val or None
    return None


def _migrate(conn, *, renames: tuple[tuple[str, str], ...], forward: bool) -> None:
    rows = conn.exec_driver_sql("SELECT book_id, payload FROM profile").fetchall()
    touched = 0
    for book_id, raw in rows:
        try:
            payload = json.loads(raw) if isinstance(raw, str) else raw
        except Exception:  # noqa: BLE001 — one unreadable profile must not stop the rest
            log.warning("profile payload for book %s is unreadable; skipped", book_id)
            continue
        if not isinstance(payload, dict):
            continue
        changed = False
        for old, new in renames:
            src, dst = (old, new) if forward else (new, old)
            if src not in payload:
                continue
            val = payload.pop(src)
            payload[dst] = _scalar_to_list(val) if forward else _list_to_scalar(val)
            changed = True
        if not changed:
            continue
        conn.exec_driver_sql(
            "UPDATE profile SET payload = ? WHERE book_id = ?",
            (json.dumps(payload), book_id),
        )
        touched += 1
    if touched:
        log.info(
            "%s %d profile payload(s)",
            "listified" if forward else "scalarized",
            touched,
        )


def upgrade() -> None:
    _migrate(op.get_bind(), renames=RENAMES, forward=True)


def downgrade() -> None:
    _migrate(op.get_bind(), renames=RENAMES, forward=False)
