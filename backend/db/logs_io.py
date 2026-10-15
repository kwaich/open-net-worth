"""Logs/telemetry import/export between the JSON file shapes and the Tier-2 log
tables (``automation_runs``, ``automation_runtime``, ``batch_ledger``,
``mcp_write_log``, ``model_telemetry``, ``pending_analysis``,
``run_history``, ``tool_call_log``) — the plan-4c task-2 absorb, revision 0014.
(Originally 9 stores including ``harness_suggestions``, dropped 2026-08-28 when
self-harness was removed — see ``docs/launch/BOARD.md``.)

Mirrors ``planning_io.py``/``investments_io.py``'s generic pair exactly, minus book
stamping: this wave carries NO ``book_id`` anywhere (``backend.db.tables``'s own
Tier-2 logs comment — operational/runtime logs, not book-scoped user data). Of these,
7 stores are flat lists absorbed by the SAME generic ``(seq, id, payload)`` pair every
flat-list store reuses; ``model_telemetry`` is a dict-shaped singleton-per-key store
(mirrors ``plan_state``/``watchlists``) — its payload IS the per-model entry
(``{samples, errors, total}``), never wrapped with the model id inside it
(``model_telemetry_store.py``'s own docstring: "no ``model`` key inside the value").

NO MONEY GUARD: ``cost_usd`` (``automation_runs``, ``run_history``) and
``base_amount`` (``mcp_write_log``) are AI-cost/audit TELEMETRY, never summed into net
worth or any financial total — verified across all 9 stores' schemas (plan 4c recon) —
so they stay payload-verbatim like every other non-promoted field (``money_io``'s own
payload rule). No ``_assert_finite_money`` call this wave, unlike ``investments_io``'s
proactive FR-M7 guard (that money DOES feed net worth).

``LOGS_PAYLOAD_SPECS``/``LOGS_FILES`` are future-facing scaffolding (mirrors
``INVESTMENT_PAYLOAD_SPECS``/``PLANNING_PAYLOAD_SPECS`` — each of those is grepped as
appearing ONLY in its own ``*_io.py`` module; neither the cutover revision nor
``portable_bridge.py`` actually iterates them) — revision 0014 and the portable bridge
both import ``backend.db.tables`` directly instead, matching what 0012/0010 and the
money/planning bridges actually do, not what this convention aspires to. They are NOT
extended for Wave 5B below (deliberately — see that section).

WAVE 5B (JSON->DB, revision 0023) adds 4 more Tier-2 logs stores to this module:
``tool_discovery_log``/``agent_findings`` (generic flat-list, reusing
``import_payload_list``/``export_payload_list`` directly — no new function needed) and
``cost_log``/``cost_log_daily``. Two of the four — ``tool_discovery_log`` and
``cost_log`` — need ``import_payload_list_minted`` instead of the plain generic pair:
unlike every source above (all either ``JsonStore``-backed with a real ``id`` field, or
dict-keyed), these two were hand-rolled ``json.loads``/``atomic_write_text`` I/O with no
id concept at all — a row was always a bare ``{ts, ...}`` dict. ``cost_log_daily`` is a
third dict-shaped store (own bespoke pair below, mirrors ``model_telemetry`` exactly).
Not added to ``LOGS_PAYLOAD_SPECS``/``LOGS_FILES``: that scaffolding's shape (``(filename,
table, id_field)``, an ``id_field`` that names a real payload key) does not fit the two
minted-id stores, and it is unused decoration regardless (previous paragraph).
"""

from __future__ import annotations

from sqlalchemy import Connection, Table, insert, select

from backend.core.ids import new_id
from backend.core.io import fire_db_write_hook
from backend.db.tables import (
    automation_runs,
    automation_runtime,
    batch_ledger,
    cost_log_daily,
    mcp_write_log,
    model_telemetry,
    pending_analysis,
    run_history,
    tool_call_log,
)

_BATCH = 1000


# ── Generic pair — 8 of the 9 Tier-2 log stores. No book_id anywhere in this wave. ──


def import_payload_list(
    conn: Connection,
    table: Table,
    raw: list[dict],
    *,
    id_field: str = "id",
    reverse: bool = False,
) -> int:
    """``reverse=True`` is for the 3 PREPEND-order sources (``mcp_write_log``,
    ``tool_call_log``, ``run_history``) whose JSON files are
    newest-first (``items.insert(0, new)``, never appended). Their store classes read
    newest-first via an explicit ``ORDER BY seq DESC`` and their post-migration writers
    assign a fresh row ``_next_seq(conn)`` = ``max(seq)+1`` (highest = newest) — so
    ascending seq must mean oldest→newest, exactly like every append-order store.
    Importing ``raw`` (newest-first) in FILE order via plain ``enumerate`` would assign
    the NEWEST row seq=0, inverting chronological order for every pre-existing row the
    first time ``seq DESC`` is read post-migration (plan 4c wave review Blocker).
    ``reverse=True`` walks ``raw`` oldest-to-newest instead — ``list(reversed(raw))`` —
    so seq=0 lands on the OLDEST row and the highest seq lands on the newest, matching
    ``_next_seq``'s convention and making a post-migration append continue the sequence
    correctly. The 5 append-order stores (``automation_runs``, ``automation_runtime``,
    ``batch_ledger``, ``pending_analysis``; ``model_telemetry`` is dict-shaped and
    unaffected) keep the default ``reverse=False`` — their JSON files are ALREADY
    oldest-first (plain ``.append()``), so plain ``enumerate`` is already correct."""
    ordered = list(reversed(raw)) if reverse else raw
    rows = [
        {"seq": seq, "id": row.get(id_field), "payload": row} for seq, row in enumerate(ordered)
    ]
    if rows:
        fire_db_write_hook(table.name, [(r["id"], None) for r in rows], conn=conn)
    for i in range(0, len(rows), _BATCH):
        conn.execute(insert(table), rows[i : i + _BATCH])
    return len(rows)


def import_payload_list_minted(
    conn: Connection,
    table: Table,
    raw: list[dict],
    *,
    id_prefix: str,
    reverse: bool = False,
) -> int:
    """Like ``import_payload_list``, for the two Wave 5B sources whose items carry NO
    natural id field at all (``tool_discovery_log.json``/``cost_log.json`` — both were
    hand-rolled ``json.loads``/``atomic_write_text`` I/O, never a ``JsonStore``, so a row
    was always a bare dict with no ``"id"`` key). ``payload_table()`` still requires a
    real, unique ``id`` column, so this mints one fresh per row via ``new_id(id_prefix)``
    — but, mirroring ``import_match_decisions``'s own surrogate-id precedent ("the
    surrogate id lives only in the id column, never the payload"), the minted id is
    NEVER mixed into the stored ``payload`` — it stays byte-identical to the source dict,
    which is what the cutover's ``export_fn(conn) == original`` round-trip check needs.
    ``reverse`` carries the same meaning as ``import_payload_list``'s own flag."""
    ordered = list(reversed(raw)) if reverse else raw
    rows = [
        {"seq": seq, "id": new_id(id_prefix), "payload": row} for seq, row in enumerate(ordered)
    ]
    if rows:
        fire_db_write_hook(table.name, [(r["id"], None) for r in rows], conn=conn)
    for i in range(0, len(rows), _BATCH):
        conn.execute(insert(table), rows[i : i + _BATCH])
    return len(rows)


def export_payload_list(conn: Connection, table: Table, *, reverse: bool = False) -> list[dict]:
    """``reverse=True`` mirrors ``import_payload_list``'s flag: read ``seq DESC`` so the
    exported array reproduces the prepend source's newest-first byte order exactly (the
    cutover's ``export_fn(conn) == original`` round-trip check needs this — importing
    reversed and then exporting ascending would silently produce the REVERSE of the
    source, still passing a naive equality check only when every row is order-invariant,
    e.g. a single-row fixture; a real multi-row source would fail loudly instead, but
    the point of this flag is to never rely on that). Must be passed the SAME
    ``reverse`` value the matching ``import_payload_list`` call used."""
    order = table.c.seq.desc() if reverse else table.c.seq
    return [r.payload for r in conn.execute(select(table).order_by(order))]


# ── model_telemetry: one row per model, keyed by the bare model id, payload is the
# entry dict verbatim (no "model" key inside it — model_telemetry_store.py's own
# hand-rolled shape, mirrors plan_state/watchlists' bespoke dict-shaped pair). ──────


def import_model_telemetry(conn: Connection, raw: dict) -> int:
    rows = [
        {"seq": seq, "id": model, "payload": entry}
        for seq, (model, entry) in enumerate((raw or {}).items())
    ]
    if rows:
        fire_db_write_hook(model_telemetry.name, [(r["id"], None) for r in rows], conn=conn)
    for i in range(0, len(rows), _BATCH):
        conn.execute(insert(model_telemetry), rows[i : i + _BATCH])
    return len(rows)


def export_model_telemetry(conn: Connection) -> dict:
    return {r.id: r.payload for r in conn.execute(select(model_telemetry))}


# ── cost_log_daily (Wave 5B): one row per "YYYY-MM-DD|model|source" bucket, keyed by
# that bare string — the SAME dict-shaped pair as model_telemetry above (id = the key,
# payload = the aggregate dict verbatim; the key is never re-derived from or echoed
# into the payload, even though the payload's own "date"/"model"/"source" fields happen
# to redundantly encode the same three components — that redundancy already existed in
# the source JSON, so it is preserved, not introduced). ───────────────────────────────


def import_cost_log_daily(conn: Connection, raw: dict) -> int:
    rows = [
        {"seq": seq, "id": key, "payload": agg}
        for seq, (key, agg) in enumerate((raw or {}).items())
    ]
    if rows:
        fire_db_write_hook(cost_log_daily.name, [(r["id"], None) for r in rows], conn=conn)
    for i in range(0, len(rows), _BATCH):
        conn.execute(insert(cost_log_daily), rows[i : i + _BATCH])
    return len(rows)


def export_cost_log_daily(conn: Connection) -> dict:
    return {r.id: r.payload for r in conn.execute(select(cost_log_daily))}


# ── Filename/table/id_field specs — future-facing scaffolding (see module docstring);
# 8 entries, not 9: model_telemetry's import is bespoke (dict-shaped singleton), so it
# is addressed by name, not through this generic spec list — mirrors
# PLANNING_PAYLOAD_SPECS/INVESTMENT_PAYLOAD_SPECS excluding their own dict-shaped
# stores for the same reason. ────────────────────────────────────────────────────────

LOGS_PAYLOAD_SPECS: list[tuple[str, Table, str]] = [
    ("automation_runs.json", automation_runs, "id"),
    ("automation_runtime.json", automation_runtime, "automation_id"),
    ("batch_ledger.json", batch_ledger, "handle"),
    ("mcp_write_log.json", mcp_write_log, "id"),
    ("pending_analysis.json", pending_analysis, "id"),
    ("run_history.json", run_history, "id"),
    ("tool_call_log.json", tool_call_log, "id"),
    # model_telemetry.json uses the bespoke dict-shaped pair above — addressed by name,
    # not through this list (mirrors PLANNING_PAYLOAD_SPECS excluding
    # budget_assignments/plan_state).
]
LOGS_FILES: tuple[str, ...] = tuple(fn for fn, _, _ in LOGS_PAYLOAD_SPECS) + (
    "model_telemetry.json",
)
