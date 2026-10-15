"""Derive ``quote_per_unit`` from ``face_value`` on every stored bond instrument.

MATH-50 (money-math audit): ADR 0020 fixes the fixed-income valuation identity as
``qty × price × quote_per_unit`` with price quoted as percent-of-par, which only works when
``quote_per_unit == bond_terms.face_value / 100``. Nothing ever derived it —
``db/instrument_store.py::_make_instrument`` stored ``face_value`` and left
``quote_per_unit`` at its generic default of ``1.0`` — so every bond minted before the
write-path fix (same change as this migration) is valued 10x-100x off: an SSB principal of
10,000 (face 1) valued at 1,000,000; 10 market bonds at clean price 99 (face 1000) valued at
990 instead of 9,900.

``instruments`` is a plain ``payload_table`` (seq/id/payload, no ``book_id`` — see
``db/tables.py``), so this is a payload rewrite, same shape as 0064
(``drop_asset_brief_news_fields``). Only rows with ``asset_class == "bond"`` AND a
``bond_terms`` dict carrying a ``face_value`` are touched; a bond somehow written with no
``bond_terms`` (or no ``face_value`` in it) is left alone — there is nothing to derive from,
and the write-path fix means no NEW instrument can be created in that state going forward.
A payload that cannot be parsed is skipped rather than failing the boot, matching every other
payload-rewrite migration in this store.

Idempotent: re-running recomputes the same value (face_value never changes here), so a
partial or repeated run is harmless.

Downgrade is a no-op — the pre-migration ``quote_per_unit == 1.0`` was never a real fact
(it was the unset default), so there is nothing correct to restore it to; reverting would
just reintroduce the MATH-50 bug on every affected row.

Revision ID: 202609051200_derive_bond_quote_per_unit
Revises: 202609032128_flip_plan_event_amount_sign
Create Date: 2026-09-05
"""

from __future__ import annotations

import json
import logging

import sqlalchemy as sa
from alembic import op

log = logging.getLogger("alembic.derive_bond_quote_per_unit")

revision = "202609051200_derive_bond_quote_per_unit"
down_revision = "202609032128_flip_plan_event_amount_sign"
branch_labels = None
depends_on = None

_SELECT = sa.text("SELECT id, payload FROM instruments")
_UPDATE = sa.text("UPDATE instruments SET payload = :payload WHERE id = :id")


def upgrade() -> None:
    conn = op.get_bind()
    touched = 0
    for row_id, raw in conn.execute(_SELECT).fetchall():
        try:
            payload = json.loads(raw) if isinstance(raw, str) else raw
        except Exception:  # noqa: BLE001 — one unreadable instrument must not stop the rest
            log.warning("instrument payload for %s is unreadable; skipped", row_id)
            continue
        if not isinstance(payload, dict) or payload.get("asset_class") != "bond":
            continue
        bond_terms = payload.get("bond_terms")
        if not isinstance(bond_terms, dict):
            continue
        face_value = bond_terms.get("face_value")
        if not isinstance(face_value, (int, float)):
            continue
        derived = face_value / 100.0
        if payload.get("quote_per_unit") == derived:
            continue
        payload["quote_per_unit"] = derived
        conn.execute(_UPDATE, {"payload": json.dumps(payload), "id": row_id})
        touched += 1
    if touched:
        log.info("derived quote_per_unit from face_value on %d bond instrument(s)", touched)


def downgrade() -> None:
    """No-op — see module docstring: the pre-migration value was an unset default, not a
    fact worth restoring, and reverting would reintroduce the MATH-50 mis-valuation."""
