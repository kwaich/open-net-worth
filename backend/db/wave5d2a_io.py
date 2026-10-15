"""Wave 5D2a (JSON->DB) import/export: ``auth``/``model_config``/``email_ingest`` (all
namespace rows in ``secret_config``) + ``app_config`` (a namespace row in
``runtime_state``) — revision 0029 absorbs; schema is revision 0028 (a documented
no-op — both destination tables already exist from Wave 5D1/5C).

Each wave gets its own self-contained io module in this codebase (mirrors
``profile_io.py``/``logs_io.py``/``config_io.py``/``wave5c_io.py``/``wave5d1_io.py`` —
every one of them redefines its own copy of the same handful of generic shapes rather
than importing a prior wave's module) — this module follows that precedent rather than
reaching into ``wave5d1_io`` for the namespaced-singleton shape it already has.

One shape, one function pair: ``import_namespaced``/``export_namespaced`` — a
namespace-keyed singleton row, generic over WHICH TABLE too (unlike
``wave5d1_io``'s copy, which is hardcoded to nothing in particular but was only ever
called against ``secret_config`` — this wave's own four jobs span two different
physical tables, so the parameter earns its keep here explicitly). All four sources
parse to a JSON **object** (dict) — a shape mismatch RAISES rather than silently
coercing or dropping data, same as every prior wave's absorb.

SECRET, NOT MONEY (the three ``secret_config`` rows) / NOT SECRET, NOT MONEY (the one
``runtime_state`` row): none of these four stores carries a net-worth-bearing amount
(auth secrets, LLM provider config, IMAP credentials, branding/feature flags) — every
field stays payload-verbatim, mirroring every prior wave's identical "no
``_assert_finite_money`` call" rule. The security properties these four stores carry
(SECRET_TABLES / NON_RESTORABLE_TABLES / checkpoint-EXCLUDED for the three
``secret_config`` rows; checkpoint-EXCLUDED only for the ``runtime_state`` row) are
enforced by ``core.sensitive``/``ai.checkpoint._policy`` at the TABLE level — this
module only ever moves bytes verbatim between a JSON file and a table row, exactly
like every other wave's io module.
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
