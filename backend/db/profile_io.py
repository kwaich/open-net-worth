"""Profile import/export between profile.json and the per-book singleton ``profile`` table
(revision 0020) — the Wave-5 (JSON->DB) absorb, revision 0021.

Mirrors ``planning_io.import_plan_state``/``export_plan_state`` exactly: a one-row-per-book
singleton keyed by ``book_id``, ``payload`` stored VERBATIM so ``export == source`` holds for the
cutover's round-trip verification. Profile amounts are user-entered projection/config inputs
(never ledger money summed into net worth), so they stay raw in ``payload`` — no ``_money_text``
decimal promotion, exactly like plan_state.
"""

from __future__ import annotations

from sqlalchemy import Connection, insert, select

from backend.core.io import fire_db_write_hook
from backend.db.tables import DEFAULT_BOOK_ID, profile


def import_profile(conn: Connection, raw: dict) -> int:
    fire_db_write_hook(profile.name, [(DEFAULT_BOOK_ID, None)], conn=conn)
    conn.execute(insert(profile).values(book_id=DEFAULT_BOOK_ID, payload=raw))
    return 1


def export_profile(conn: Connection) -> dict:
    row = conn.execute(select(profile).where(profile.c.book_id == DEFAULT_BOOK_ID)).first()
    return row.payload if row else {}
