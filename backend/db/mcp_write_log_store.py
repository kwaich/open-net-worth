"""McpWriteLogStore — the append-only AUDIT log of MUTATING inbound-MCP calls.

When an inbound MCP ``tools/call`` runs a tool that declares ``mutates=(…)``, the adapter
records one row here so the write is visible + attributable: which surface, which tool and
data domains it touched, a no-secret args summary, the base amount (money rows), the
``checkpoint_id`` it is undoable from, and whether it succeeded.

This is **observability, not a permission gate** — it records what happened, and can refuse
nothing. (It outlived the MCP grant/tier system it was born alongside precisely because it was
never part of the gate.)

A capped ring, newest first, bounded. It is **runtime / behavioral output state →
checkpoint-EXCLUDED** (``ai/checkpoint._EXCLUDED_TABLES`` — unlike most runtime logs it IS
written *inside* the CheckpointContext the adapter opens around the call, so it must be
excluded explicitly). The store is dumb shape-only.

Row schema:
    id            : str
    ts            : ISO
    surface       : str          "api_token" | "server:<id>"
    tool          : str          the mutating tool that ran
    domains       : list[str]    its declared ``mutates`` data domains
    args_summary  : str          short, no secrets
    base_amount   : float | None money rows only (recomputed in base by the caller) — stays a
                                   raw payload value, never summed into net worth (AI-cost/audit
                                   telemetry only, like ``automation_runs.cost_usd``)
    checkpoint_id : str | None   the checkpoint the write is undoable from
    result_ok     : bool         did the underlying call succeed

PLAN 4C TASK 1: DB-backed (Tier-2 logs, table ``mcp_write_log``) via ``PayloadTableStore``.
Newest-first via an explicit ``ORDER BY seq DESC`` read (Design Note 2, plan 4c) — the
ORIGINAL list-prepend order.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import delete as sa_delete
from sqlalchemy import func, select
from sqlalchemy import insert as sa_insert

from backend.core.ids import new_id
from backend.db.payload_store import PayloadTableStore
from backend.db.tables import mcp_write_log

MAX_WRITE_LOG = 500


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _make_row(data: dict) -> dict:
    return {
        "id": data.get("id") or new_id("mcpw"),
        "ts": data.get("ts") or _now(),
        "surface": data.get("surface") or "api_token",
        "tool": data.get("tool", ""),
        "domains": list(data.get("domains") or []),
        "args_summary": data.get("args_summary") or "",
        "base_amount": data.get("base_amount"),
        "checkpoint_id": data.get("checkpoint_id"),
        "result_ok": bool(data.get("result_ok", False)),
    }


class McpWriteLogStore(PayloadTableStore):
    def __init__(self, engine=None):
        super().__init__(mcp_write_log, id_field="id", engine=engine)

    def recent(self, limit: int = 50) -> list[dict]:
        """The audit feed, newest first, capped at ``limit``."""
        with self._eng().connect() as conn:
            rows = conn.execute(
                select(self._table).order_by(self._table.c.seq.desc()).limit(max(0, limit))
            )
            return [r.payload for r in rows]

    def append(self, data: dict) -> dict:
        """Append a new audit row, capping at ``MAX_WRITE_LOG`` (oldest, lowest-seq, dropped)
        — all inside ONE base transaction (the read-modify-write cycle the removed
        _write_lock/_load_items/_save_items plumbing used to provide)."""
        row = _make_row(data)
        with self._use_conn(None) as conn:
            self._fire_hook(conn, [(row["id"], None)])
            seq = self._next_seq(conn)
            conn.execute(sa_insert(self._table).values(**self._insert_row(seq, row["id"], row)))
            over = (
                conn.execute(select(func.count()).select_from(self._table)).scalar_one()
                - MAX_WRITE_LOG
            )
            if over > 0:
                stale = [
                    r.id
                    for r in conn.execute(
                        select(self._table.c.id).order_by(self._table.c.seq).limit(over)
                    )
                ]
                conn.execute(sa_delete(self._table).where(self._table.c.id.in_(stale)))
        return row
