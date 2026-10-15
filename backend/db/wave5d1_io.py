"""Wave 5D1 (JSON->DB) import/export: the 15 connector-credential configs + connector_sessions
(all namespace rows in ``secret_config``) + ``mcp_clients`` — the secrets/config slice of the
migration (revision 0027 absorbs; schema is revision 0026).

Each wave gets its own self-contained io module in this codebase (mirrors ``profile_io.py``/
``logs_io.py``/``config_io.py``/``planning_io.py``/``investments_io.py``/``misc_io.py``/
``wave5c_io.py`` — every one of them redefines its own copy of the same handful of generic
shapes rather than importing a prior wave's module) — this module follows that precedent
rather than reaching into ``wave5c_io`` for the namespaced-singleton shape it already has.

Two shapes, one function pair each:

- ``import_namespaced``/``export_namespaced`` — a namespace-keyed singleton row, generic over
  WHICH namespace (all 15 connector configs + connector_sessions share the ONE physical
  ``secret_config`` table, one call per namespace) — the identical shape
  ``config_io.import_namespaced``/``wave5c_io.import_namespaced`` already ride, redefined here
  per this module's own file's stated precedent rather than imported. Every one of the 16
  sources parses to a JSON **object** (dict) — a shape mismatch RAISES rather than silently
  coercing or dropping data, same as every prior wave's absorb.
- ``import_payload_list``/``export_payload_list`` — the generic flat-list (seq, id, payload)
  pair, natural id (``mcp_clients`` — a JSON **list**, one dict per configured external MCP
  server, keyed by its own stable ``id`` field) — mirrors every prior wave's identical helper.

SECRET, NOT MONEY: none of these 16 stores carries a net-worth-bearing amount (every field is
connector config: tokens, urls, poll intervals, account-id maps, allowlists) — every field
stays payload-verbatim, mirroring every prior wave's identical "no ``_assert_finite_money``
call" rule. The security property these 16 stores DO carry — SECRET_TABLES / NON_RESTORABLE_
TABLES / checkpoint-EXCLUDED — is enforced by ``core.sensitive``/``ai.checkpoint._policy``, not
by this io module; this module only ever moves bytes verbatim between a JSON file and a table
row, exactly like every other wave's io module.
"""

from __future__ import annotations

from sqlalchemy import Connection, Table, insert, select

from backend.core.io import fire_db_write_hook

_BATCH = 1000


# ── Namespace-keyed singleton — the 15 connector configs + connector_sessions, all rows
# in the ONE secret_config table. ──────────────────────────────────────────────────────


def import_namespaced(conn: Connection, table: Table, namespace: str, raw: dict) -> int:
    fire_db_write_hook(table.name, [(namespace, None)], conn=conn)
    conn.execute(insert(table).values(namespace=namespace, payload=raw))
    return 1


def export_namespaced(conn: Connection, table: Table, namespace: str) -> dict:
    row = conn.execute(select(table).where(table.c.namespace == namespace)).first()
    return row.payload if row else {}


# ── Generic flat list, natural id, append-order — mcp_clients. ─────────────────────────


def import_payload_list(
    conn: Connection, table: Table, raw: list[dict], *, id_field: str = "id"
) -> int:
    rows = [{"seq": seq, "id": row.get(id_field), "payload": row} for seq, row in enumerate(raw)]
    if rows:
        fire_db_write_hook(table.name, [(r["id"], None) for r in rows], conn=conn)
    for i in range(0, len(rows), _BATCH):
        conn.execute(insert(table), rows[i : i + _BATCH])
    return len(rows)


def export_payload_list(conn: Connection, table: Table) -> list[dict]:
    return [r.payload for r in conn.execute(select(table).order_by(table.c.seq))]
