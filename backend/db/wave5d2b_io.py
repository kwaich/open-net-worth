"""Wave 5D2b (JSON->DB) import/export: pending_plans, mcp_pending_calls, chat_pairing —
the permission/nonce machinery slice of the migration (revision 0031 absorbs; schema is
revision 0030).

Each wave gets its own self-contained io module in this codebase (mirrors
``profile_io.py``/``logs_io.py``/``config_io.py``/``wave5c_io.py``/``wave5d1_io.py``/
``wave5d2a_io.py`` — every one of them redefines its own copy of the same handful of
generic shapes rather than importing a prior wave's module) — this module follows that
precedent rather than reaching into ``wave5c_io`` for the keyed-dict shape it already has
(``skills_state``'s own pair).

One shape, one function pair: ``import_keyed``/``export_keyed`` — a dict keyed by an
external id (a token, a tool_use_id, a nonce, or a "<connector>:<sender_id>" composite
key) — mirrors ``wave5c_io.import_keyed``/``export_keyed`` exactly (``skills_state``'s own
pair). All three sources parse to a JSON **object** (dict) — a shape mismatch RAISES
rather than silently coercing or dropping data, same as every prior wave's absorb.

NO MONEY GUARD: none of these three stores carries a net-worth-bearing amount directly —
``pending_plans``/``mcp_pending_calls`` stage TOOL ARGS (which may themselves describe a
money-moving call, e.g. ``add_transactions``), never a bare amount field of their own;
``chat_pairing`` holds a pairing code — every field stays payload-verbatim, mirroring
every prior wave's identical "no ``_assert_finite_money`` call" rule; the historical-rate
lock / txn_base_amount recomputation this codebase cares about is a property of the
LEDGER these staged calls eventually write to, not of the staging row itself.

NOT SECRET, NON-RESTORABLE: the security properties these three stores carry
(NON_RESTORABLE_TABLES, checkpoint-EXCLUDED — never SECRET_TABLES, none holds a bearer
credential) are enforced by ``core.sensitive``/``ai.checkpoint._policy`` at the TABLE
level — this module only ever moves bytes verbatim between a JSON file and a table row,
exactly like every other wave's io module.
"""

from __future__ import annotations

from sqlalchemy import Connection, Table, insert, select

from backend.core.io import fire_db_write_hook

_BATCH = 1000


def import_keyed(conn: Connection, table: Table, raw: dict) -> int:
    rows = [
        {"seq": seq, "id": key, "payload": value}
        for seq, (key, value) in enumerate((raw or {}).items())
    ]
    if rows:
        fire_db_write_hook(table.name, [(r["id"], None) for r in rows], conn=conn)
    for i in range(0, len(rows), _BATCH):
        conn.execute(insert(table), rows[i : i + _BATCH])
    return len(rows)


def export_keyed(conn: Connection, table: Table) -> dict:
    return {r.id: r.payload for r in conn.execute(select(table))}
