"""wave5b logs cutover: absorb tool_discovery_log.json, cost_log.json,
cost_log_daily.json and agent_findings.json into their revision-0022 tables, with
round-trip verification. Wave 5B (JSON->DB).

Mirrors 0014's (logs cutover) ``_absorb_one``/``_stamp_cutover`` exactly, four stores:
read each present source's bytes ONCE (hash + parse the SAME bytes), import, EXACT
round-trip verify (``export_fn(conn) == original``) — a diff RAISES, aborting inside
Alembic's one transaction, and the fail-closed ``ensure_migrated`` wrapper then restores
the pre-migrate backup. No cross-file consistency check (no FKs between these four, or
to anything else) — absorption order is COSMETIC. No money guard (``logs_io.py``'s own
docstring — ``cost_usd`` here is telemetry, never net worth).

``cost_log_daily.json`` parses to a JSON **object** (dict, keyed ``date|model|source``)
— checked with ``isinstance(data, dict)`` (0014's own ``model_telemetry.json`` precedent,
inverted for this one store); the other three sources parse to a flat **list**. A shape
mismatch RAISES rather than silently coercing or dropping data.

``tool_discovery_log.json``/``cost_log.json`` go through ``import_payload_list_minted``
(0014's own ``reverse``-flag machinery does not apply here — these two sources are
APPEND-order, oldest-first on disk, matching the flag's own default): neither source's
items ever carried an ``id`` field (both were hand-rolled file I/O, never a
``JsonStore``), so a fresh id is minted per row at import time, living only in the row's
``id`` column — the payload stays byte-identical to the source dict, which is what the
round-trip check below needs. ``agent_findings.json`` DOES carry a natural ``id`` field
per item (its retired store always minted one at write time) and is PREPEND-order
(newest-first on disk, like ``harness_suggestions``/``run_history``), so it uses the
plain generic ``import_payload_list``/``export_payload_list`` pair with
``reverse=True`` — exactly 0014's own mechanism for that shape.

Only once every present store's import + round-trip verification has succeeded does a
``json_cutovers`` row land per store (source hash included), so ``_finish_cutovers``
renames each source to ``<name>.migrated-<ts>`` after ``upgrade head`` fully succeeds,
ONLY if the bytes still hash the same.

Revision ID: 0023
Revises: 0022
Create Date: 2026-08-10
"""

import hashlib
import json
from datetime import datetime, timezone

import sqlalchemy as sa
from alembic import op

revision = "202608110403_wave5b_logs_cutover"
down_revision = "202608110402_wave5b_logs_schema"
branch_labels = None
depends_on = None


def _absorb_one(conn, store: str, src, shape: type, import_fn, export_fn):
    """Verbatim port of 0014's own helper (see that revision for the full mechanics
    the sparser comments below rely on)."""
    if not src.exists():
        return None
    raw = src.read_bytes()  # ONE read: the hash below and the parse see identical bytes
    original = json.loads(raw.decode("utf-8"))
    if not isinstance(original, shape):
        expected = "a flat JSON list" if shape is list else "a JSON object"
        raise RuntimeError(
            f"wave5b logs cutover: {src} is not {expected} ({shape.__name__} expected at "
            f"the root) — got {type(original).__name__}; aborting rather than silently "
            "dropping data"
        )
    import_fn(conn, original)
    if export_fn(conn) != original:
        raise RuntimeError(
            f"wave5b logs cutover: {store} round-trip verification FAILED — aborting the "
            "whole migration (all four sources are one all-or-nothing unit); the "
            "fail-closed wrapper will restore the pre-migrate backup"
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
    from backend.db.tables import agent_findings, cost_log, tool_discovery_log

    conn = op.get_bind()
    now = datetime.now(timezone.utc).isoformat()

    jobs = [
        (
            "tool_discovery_log",
            evsrc.TOOL_DISCOVERY_LOG_SOURCE,
            list,
            lambda c, raw: lio.import_payload_list_minted(
                c, tool_discovery_log, raw, id_prefix="tdisc"
            ),
            lambda c: lio.export_payload_list(c, tool_discovery_log),
        ),
        (
            "cost_log",
            evsrc.COST_LOG_SOURCE,
            list,
            lambda c, raw: lio.import_payload_list_minted(c, cost_log, raw, id_prefix="cost"),
            lambda c: lio.export_payload_list(c, cost_log),
        ),
        (
            "cost_log_daily",
            evsrc.COST_LOG_DAILY_SOURCE,
            dict,
            lio.import_cost_log_daily,
            lio.export_cost_log_daily,
        ),
        (
            "agent_findings",
            evsrc.AGENT_FINDINGS_SOURCE,
            list,
            lambda c, raw: lio.import_payload_list(
                c, agent_findings, raw, id_field="id", reverse=True
            ),
            lambda c: lio.export_payload_list(c, agent_findings, reverse=True),
        ),
    ]

    for store, src, shape, import_fn, export_fn in jobs:
        result = _absorb_one(conn, store, src, shape, import_fn, export_fn)
        if result is not None:
            raw, _original = result
            _stamp_cutover(conn, store, src, raw, now)


def downgrade() -> None:
    """No-op: revision 0022 owns the DDL for all four tables and drops them when a
    caller downgrades past it — there is nothing for this data-only revision to undo
    beyond its own upgrade()'s row inserts, which vanish along with the tables. The
    renamed ``<name>.migrated-<ts>`` sources (once ``ensure_migrated()`` has run for
    real, not just a bare ``command.upgrade``) are the manual restore path, exactly as
    every other cutover revision in this family documents — an automatic,
    data-destructive downgrade here would be a lie."""
