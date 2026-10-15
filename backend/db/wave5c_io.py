"""Wave 5C (JSON->DB) import/export: dedup_config, harness_state, skills_state,
subscriptions, actions — the runtime queues/state slice of the migration (revision
0025 absorbs; schema is revision 0024).

Each wave gets its own self-contained io module in this codebase (mirrors
``profile_io.py``/``logs_io.py``/``config_io.py``/``planning_io.py``/``investments_io.py``/
``misc_io.py`` — every one of them redefines its own copy of the same handful of generic
shapes rather than importing a prior wave's module) — this module follows that precedent
rather than reaching into ``config_io``/``logs_io`` for a shape they already have.

Four distinct shapes, one function pair each:

- ``import_namespaced``/``export_namespaced`` — a namespace-keyed singleton row, generic
  over WHICH table (``settings`` for ``dedup_config``, ``runtime_state`` for
  ``harness_state``) — mirrors ``config_io.import_namespaced``/``export_namespaced``
  exactly, except parameterized by ``table`` instead of hardcoded to ``settings``, since
  this wave needs the SAME shape over two different physical tables.
- ``import_keyed``/``export_keyed`` — a dict keyed by an external id (``skills_state``,
  ``{skill_id: {enabled, author, created_at, updated_at, invocation_count}}``) — mirrors
  ``logs_io.import_model_telemetry``/``export_model_telemetry``'s dict-shaped pair.
- ``import_payload_list``/``export_payload_list`` — the generic flat-list (seq, id,
  payload) pair, natural id, append-order (``subscriptions`` — a plain JsonStore-backed
  flat list pre-retirement, oldest-first on disk) — mirrors every prior wave's identical
  helper.
- ``import_actions``/``export_actions`` — bespoke: ``actions.json``'s natural ``id`` field
  is a PLAIN INTEGER (confirmed by ``ai/tools/portfolio.py``'s own tool schema — "these
  ids are plain integers, not the xxx_<hex> ids other stores use"), the first payload_table
  store this migration has hit with a non-string natural id. ``payload_table()``'s ``id``
  column is ``Text`` — passing a bare Python ``int`` as its bound value works by accident
  under SQLite's flexible column-affinity coercion but is NOT guaranteed portable to
  Postgres (CI-tested from revision 1 — CLAUDE.md), so this pair explicitly ``str()``-
  coerces the id COLUMN only; the stored ``payload`` keeps the real JSON int verbatim
  (round-trip fidelity — the cutover's ``export == source`` check compares payloads, not
  id columns). Mirrors ``ActionStore._insert_row``'s live-write-path coercion exactly, so
  a row absorbed by the cutover and a row written afterward carry an identically-typed id
  column.

NO MONEY GUARD: none of these five stores carries a net-worth-bearing amount (dedup_config
is engine/LLM settings, harness_state a timestamp, skills_state per-skill flags,
subscriptions a dismiss/confirm overlay, actions a property/commitment reminder log with
no amount field) — every field stays payload-verbatim, mirroring every prior wave's
identical "no _assert_finite_money call" rule.
"""

from __future__ import annotations

from sqlalchemy import Connection, Table, insert, select

from backend.core.io import fire_db_write_hook

_BATCH = 1000


# ── Namespace-keyed singleton — dedup_config (table=settings), harness_state
# (table=runtime_state). ──────────────────────────────────────────────────────────────


def import_namespaced(conn: Connection, table: Table, namespace: str, raw: dict) -> int:
    fire_db_write_hook(table.name, [(namespace, None)], conn=conn)
    conn.execute(insert(table).values(namespace=namespace, payload=raw))
    return 1


def export_namespaced(conn: Connection, table: Table, namespace: str) -> dict:
    row = conn.execute(select(table).where(table.c.namespace == namespace)).first()
    return row.payload if row else {}


# ── Keyed dict — skills_state (id = skill id, payload = the state dict verbatim). ──────


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


# ── Generic flat list, natural id, append-order — subscriptions. ───────────────────────


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


# ── actions — flat list, natural id, but the id is a plain int (see module docstring). ──


def import_actions(conn: Connection, table: Table, raw: list[dict]) -> int:
    rows = [{"seq": seq, "id": str(row.get("id")), "payload": row} for seq, row in enumerate(raw)]
    if rows:
        fire_db_write_hook(table.name, [(r["id"], None) for r in rows], conn=conn)
    for i in range(0, len(rows), _BATCH):
        conn.execute(insert(table), rows[i : i + _BATCH])
    return len(rows)


def export_actions(conn: Connection, table: Table) -> list[dict]:
    return [r.payload for r in conn.execute(select(table).order_by(table.c.seq))]
