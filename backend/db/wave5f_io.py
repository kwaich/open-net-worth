"""Wave 5F (JSON->DB) import/export: the three CACHE_DIR runtime-state watermarks —
digest_state, news_screen_state, memory_consolidate_state — absorbed into the
`runtime_state` table as three more namespace rows (revision 0037; schema is the
documented no-op revision 0036, since `runtime_state` already exists from Wave 5C).

Mirrors `wave5c_io.import_namespaced`/`export_namespaced` exactly (this codebase's own
precedent: "Each wave gets its own self-contained io module... rather than reaching into
config_io/logs_io for a shape they already have" — wave5c_io.py's own docstring). All
three sources here share ONE table (`runtime_state`), unlike wave5c_io's `table=`
parameter which varied between `settings` and `runtime_state` — kept as a parameter
anyway for the same "generic over which table" reason, in case a future namespace in this
wave ever needs `settings` instead.

NO MONEY GUARD: none of these three watermarks carries a net-worth-bearing amount (a
delivery date, a screen-budget counter, a fact-count watermark) — every field stays
payload-verbatim, mirroring every prior wave's identical rule.
"""

from __future__ import annotations

from sqlalchemy import Connection, Table, insert, select

from backend.core.io import fire_db_write_hook


def import_namespaced(conn: Connection, table: Table, namespace: str, raw: dict) -> int:
    fire_db_write_hook(table.name, [(namespace, None)], conn=conn)
    conn.execute(insert(table).values(namespace=namespace, payload=raw))
    return 1


def export_namespaced(conn: Connection, table: Table, namespace: str) -> dict:
    row = conn.execute(select(table).where(table.c.namespace == namespace)).first()
    return row.payload if row else {}
