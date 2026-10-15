"""Portable-bundle import/export for the ``budget_engine_state`` table (revision 0049) — a
NEW addition, sourced independently rather than folded into ``planning_io.py``'s revision-0010
absorb set (mirrors ``profile_io.py``'s own precedent: a table added AFTER the original
planning wave gets its own small io module, even though its shape echoes an existing bespoke
pair — here, ``planning_io.import_budget_assignments``/``export_budget_assignments``).

Bundle shape is a bare dict, ``{engine_id: payload}`` — one level flatter than
``budget_assignments``' nested ``{month: {category: amount}}`` (there is no second key here;
an engine's own state is a single opaque JSON blob), but the same "keyed relation, no ``seq``"
shape: ``id`` is the deterministic ``f"{book_id}:{engine_id}"`` surrogate (``db/tables.py``),
never round-tripped through the bundle itself — only ``engine_id``/``payload`` travel.
``payload`` is stored and returned VERBATIM (no ``_money_text`` decimal promotion — this store
holds configuration, not ledger money; see ``db/tables.py``'s own comment on the table).
"""

from __future__ import annotations

from sqlalchemy import Connection, insert, select

from backend.core.io import fire_db_write_hook
from backend.db.tables import DEFAULT_BOOK_ID, budget_engine_state

_BATCH = 1000


def _row_id(engine_id: str) -> str:
    return f"{DEFAULT_BOOK_ID}:{engine_id}"


def import_budget_engine_state(conn: Connection, raw: dict) -> int:
    rows = [
        {
            "id": _row_id(engine_id),
            "book_id": DEFAULT_BOOK_ID,
            "engine_id": engine_id,
            "payload": payload,
        }
        for engine_id, payload in (raw or {}).items()
    ]
    if rows:
        fire_db_write_hook(budget_engine_state.name, [(r["id"], None) for r in rows], conn=conn)
    for i in range(0, len(rows), _BATCH):
        conn.execute(insert(budget_engine_state), rows[i : i + _BATCH])
    return len(rows)


def export_budget_engine_state(conn: Connection) -> dict:
    return {r.engine_id: r.payload for r in conn.execute(select(budget_engine_state))}
