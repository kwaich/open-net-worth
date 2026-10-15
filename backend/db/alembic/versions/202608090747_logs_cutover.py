"""logs cutover: absorb the 9 JSON log/telemetry sources into their revision-0013
tables (plan 4c, task 2). Mirrors 0012 (investments cutover) exactly: read every
present source's bytes ONCE, import, EXACT round-trip verify (export == original),
aborting the WHOLE migration on any diff. No cross-file consistency check (no FKs
between these 9, or to anything else) — absorption order is COSMETIC. No money guard
(see logs_io.py's docstring — cost_usd/base_amount here are telemetry, never net worth).

``model_telemetry.json`` parses to a JSON **object** (dict) — checked with
``isinstance(data, dict)`` (0005's/0010's/0012's own ``isinstance(..., list)`` guard,
inverted for this one store) — the other 8 sources parse to a flat **list**. A shape
mismatch RAISES rather than silently coercing or dropping data.

DIRECTION FIX (wave review Blocker, fixed before this revision ever ran against a real
tree): 4 of the 8 flat-list sources — ``harness_suggestions``, ``mcp_write_log``,
``run_history``, ``tool_call_log`` — are PREPEND-order on disk (newest-first) and their
store classes read them back via ``ORDER BY seq DESC``. Those 4 pass ``reverse=True``
to ``_generic`` below so ascending seq means oldest→newest (matching every append-order
store's own convention, and ``_next_seq``'s ``max(seq)+1`` for a post-migration
append); a plain file-order import would have handed the newest pre-existing row
seq=0, inverting chronological order the first time ``seq DESC`` was read — see
``logs_io.import_payload_list``'s own docstring for the full mechanics. The round-trip
``export == original`` check below still holds byte-exact because the matching
``export_fn`` reads back ``seq DESC`` for those same 4 stores.

Only once every present store's import + round-trip verification has succeeded does a
``json_cutovers`` row land per store (source hash included), so ``_finish_cutovers``
renames each source to ``<name>.migrated-<ts>`` after ``upgrade head`` fully succeeds,
ONLY if the bytes still hash the same.

Revision ID: 0014
Revises: 0013
Create Date: 2026-08-09
"""

import hashlib
import json
from datetime import datetime, timezone

import sqlalchemy as sa
from alembic import op

revision = "202608090747_logs_cutover"
down_revision = "202608090721_logs_schema"
branch_labels = None
depends_on = None


def _absorb_one(conn, store: str, src, shape: type, import_fn, export_fn):
    """Verbatim port of 0012's own helper (see that revision for the full mechanics
    the sparser comments below rely on)."""
    if not src.exists():
        return None
    raw = src.read_bytes()  # ONE read: the hash below and the parse see identical bytes
    original = json.loads(raw.decode("utf-8"))
    if not isinstance(original, shape):
        expected = "a flat JSON list" if shape is list else "a JSON object"
        raise RuntimeError(
            f"logs cutover: {src} is not {expected} ({shape.__name__} expected at the root) "
            f"— got {type(original).__name__}; aborting rather than silently dropping data"
        )
    import_fn(conn, original)
    if export_fn(conn) != original:
        raise RuntimeError(
            f"logs cutover: {store} round-trip verification FAILED — aborting the whole "
            "migration (all nine log sources are one all-or-nothing unit); the fail-closed "
            "wrapper will restore the pre-migrate backup"
        )
    return raw, original


def _stamp_cutover(conn, store: str, src, raw: bytes, now: str) -> None:
    conn.execute(
        sa.text(
            "INSERT INTO json_cutovers (store, source_path, source_hash, migrated_at, renamed) "
            "VALUES (:s, :p, :h, :t, 0)"
        ),
        {"s": store, "p": str(src), "h": hashlib.sha256(raw).hexdigest(), "t": now},
    )


def upgrade() -> None:
    from backend.db import logs_io as lio
    from backend.db.alembic import versions_evidence_source as evsrc
    from backend.db.tables import (
        automation_runs,
        automation_runtime,
        batch_ledger,
        mcp_write_log,
        pending_analysis,
        run_history,
        tool_call_log,
    )

    conn = op.get_bind()
    now = datetime.now(timezone.utc).isoformat()

    def _generic(store: str, table, id_field: str = "id", *, reverse: bool = False):
        return (
            lambda c, raw: lio.import_payload_list(
                c, table, raw, id_field=id_field, reverse=reverse
            ),
            lambda c: lio.export_payload_list(c, table, reverse=reverse),
        )

    # (store, source path, expected root shape, import fn, export fn) — order is
    # COSMETIC (no FK between logs tables, no FK to anything else either).
    #
    # POST-HOC EDIT (2026-08-28): the "harness_suggestions" job that used to sit here was
    # removed along with the rest of self-harness (see docs/launch/BOARD.md) — its table
    # (revision 0013) was dropped by a later revision, and this revision's job list
    # imports live `backend.db.tables` Table objects, so leaving the reference here would
    # break `alembic upgrade head` on a fresh database (ImportError: harness_suggestions no
    # longer exists in db.tables). Same precedent as revision 0016's tool_grants.json
    # absorb, edited the same way when THAT table was later dropped. On any database that
    # already ran this revision historically, nothing changes — this only affects a
    # from-scratch replay of the full chain.
    #
    # reverse=True (mcp_write_log/run_history/tool_call_log ONLY): these 3 sources are
    # PREPEND-order JSON (items.insert(0, new) — newest-first on
    # disk) and their store classes read newest-first via an explicit `ORDER BY seq
    # DESC`. Importing them in plain file order via enumerate() would hand the NEWEST
    # row seq=0, inverting chronological order the first time `seq DESC` is read
    # post-migration (wave review Blocker — empirically reproduced against the live
    # tree's tool_call_log/run_history). reverse=True walks the source oldest-to-newest
    # instead, so ascending seq means oldest->newest, matching every append-order
    # store's own convention and `_next_seq`'s `max(seq)+1` (append = newest). The
    # matching export_fn reads `seq DESC` so it still reproduces the source's
    # newest-first byte order — `export == original` round-trip verification below
    # still holds byte-exact. The other 5 stores (automation_runs, automation_runtime,
    # batch_ledger, pending_analysis; model_telemetry is dict-shaped, bespoke import
    # below) are APPEND-order sources (plain `.append()`, oldest-first on disk already)
    # and keep the default reverse=False — unaffected by this fix.
    jobs = [
        (
            "automation_runs",
            evsrc.AUTOMATION_RUNS_SOURCE,
            list,
            *_generic("automation_runs", automation_runs),
        ),
        (
            "automation_runtime",
            evsrc.AUTOMATION_RUNTIME_SOURCE,
            list,
            *_generic("automation_runtime", automation_runtime, id_field="automation_id"),
        ),
        (
            "batch_ledger",
            evsrc.BATCH_LEDGER_SOURCE,
            list,
            *_generic("batch_ledger", batch_ledger, id_field="handle"),
        ),
        (
            "mcp_write_log",
            evsrc.MCP_WRITE_LOG_SOURCE,
            list,
            *_generic("mcp_write_log", mcp_write_log, reverse=True),
        ),
        (
            "pending_analysis",
            evsrc.PENDING_ANALYSIS_SOURCE,
            list,
            *_generic("pending_analysis", pending_analysis),
        ),
        (
            "run_history",
            evsrc.RUN_HISTORY_SOURCE,
            list,
            *_generic("run_history", run_history, reverse=True),
        ),
        (
            "tool_call_log",
            evsrc.TOOL_CALL_LOG_SOURCE,
            list,
            *_generic("tool_call_log", tool_call_log, reverse=True),
        ),
        (
            "model_telemetry",
            evsrc.MODEL_TELEMETRY_SOURCE,
            dict,
            lio.import_model_telemetry,
            lio.export_model_telemetry,
        ),
    ]

    for store, src, shape, import_fn, export_fn in jobs:
        result = _absorb_one(conn, store, src, shape, import_fn, export_fn)
        if result is not None:
            raw, _original = result
            _stamp_cutover(conn, store, src, raw, now)


def downgrade() -> None:
    """No-op: revision 0013 owns the DDL for all nine logs tables and drops them when a
    caller downgrades past it — there is nothing for this data-only revision to undo
    beyond its own upgrade()'s row inserts, which vanish along with the tables. The
    renamed ``<name>.migrated-<ts>`` sources (once ``ensure_migrated()`` has run for
    real, not just a bare ``command.upgrade``) are the manual restore path, exactly as
    every other cutover revision in this family documents — an automatic,
    data-destructive downgrade here would be a lie."""
