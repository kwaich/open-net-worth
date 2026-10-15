"""Read-only SQL access — the POLICY layer.

Spec: ``docs/superpowers/specs/2026-08-23-lucius-sql-access-design.md``.
Execution and enforcement live one layer down in ``db/sql_console.py``; this module owns the
three things that are decisions rather than mechanics: whether the feature is on, what the
model must be told about the numbers it just read, and what the owner gets to see afterwards.

## The caveats are the interesting part

``CLAUDE.md`` states an invariant this feature deviates from: *"NEVER add a caller-chosen
fields/projection parameter to a money-bearing read. It lets the model pick ``amount`` over
``base_amount`` and defeat the historical-rate lock."* Raw SQL is the maximal caller-chosen
projection, so the deviation is real and needed an explicit answer rather than silence (ADR
``2026-08-23-lucius-reads-the-database-directly``).

The answer is: **the invariant stands, and this tool is never a source for a reported figure.**
That is enforced in the model's context on EVERY result rather than stated once in a tool
description that scrolls away — a description is read at offer time, a caveat is read at the
moment the wrong conclusion would be drawn.

Two caveats, both load-bearing:

* **The historical-rate lock.** A stored ``base_amount`` is TRUSTED only while its
  ``base_currency`` still matches the current base. ``SELECT SUM(base_amount)`` silently skips
  ``txn_base_amount()`` and can return a confidently wrong number.
* **Soft deletes.** ``transactions`` retains ``deleted=1`` rows and rows merged away via
  ``merged_into``. A naive ``SELECT … FROM transactions`` over-counts. This is not theoretical;
  it is the single most likely way this tool produces a wrong answer.
"""

from __future__ import annotations

import logging
import re

from backend.core.errors import ValidationError
from backend.db import sql_console, sql_introspect
from backend.db.app_config_store import sql_query_enabled
from backend.db.sql_query_log_store import SqlQueryLogStore

logger = logging.getLogger(__name__)

DISABLED_MESSAGE = (
    "Direct SQL access is turned off for this instance. The owner can enable it in "
    "Settings > Developer > SQL access."
)

# The money columns on `transactions` whose values are only meaningful with the
# historical-rate lock in mind.
MONEY_COLUMNS = frozenset(
    {"base_amount", "from_amount", "to_amount", "original_amount", "fx_rate", "quantity"}
)

RATE_LOCK_CAVEAT = (
    "MONEY: a stored base_amount is only valid while that row's base_currency still equals the "
    "current base currency — a row stamped with an older base must be recomputed via "
    "txn_base_amount(). Amounts are exact decimal TEXT, so comparing or summing them in SQL "
    "may not do what you expect. Do NOT report a figure from this tool as fact: verify it with "
    "get_transactions / net_worth first."
)

SOFT_DELETE_CAVEAT = (
    "ROWS: `transactions` retains soft-deleted rows (deleted = 1) and rows merged away "
    "(merged_into IS NOT NULL). Neither filter is applied for you — add "
    "`WHERE deleted = 0 AND merged_into IS NULL` unless you specifically want them."
)

PAYLOAD_CAVEAT = (
    "SHAPE: most stores keep their real fields inside a `payload` JSON column, not as real "
    "columns. Use json_extract(payload, '$.field') and call describe_db_schema('<table>') to "
    "see which keys exist."
)

_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def _tokens(sql: str) -> set[str]:
    return {t.lower() for t in _IDENT_RE.findall(sql)}


def caveats_for(sql: str) -> list[str]:
    """The caveats this statement earns. Order is deliberate: the money warning first,
    because it is the one whose absence produces a confidently wrong answer."""
    tokens = _tokens(sql)
    out: list[str] = []
    if "transactions" in tokens:
        if tokens & MONEY_COLUMNS:
            out.append(RATE_LOCK_CAVEAT)
        out.append(SOFT_DELETE_CAVEAT)
    elif tokens & MONEY_COLUMNS:
        out.append(RATE_LOCK_CAVEAT)
    if "payload" in tokens:
        out.append(PAYLOAD_CAVEAT)
    return out


def _log(**fields) -> None:
    """Record the attempt. A logging failure must never turn a working read into an error —
    the audit trail is for the owner, and losing one row is strictly better than refusing a
    query the policy already allowed."""
    try:
        SqlQueryLogStore().append(fields)
    except Exception:  # noqa: BLE001 — see docstring
        logger.warning("sql_query: could not record the query log row", exc_info=True)


def run_query(
    sql: str,
    limit: int | None = None,
    *,
    origin: str = "unknown",
    session_id: str | None = None,
) -> dict:
    """Execute one read-only statement, caveated and logged.

    Raises ``ValidationError`` when the feature is off or the statement is refused — the
    refusal is logged before it is raised, so the owner sees attempts, not just successes.
    """
    if not sql_query_enabled():
        raise ValidationError(DISABLED_MESSAGE)
    try:
        result = sql_console.execute_readonly(sql, limit)
    except ValidationError as exc:
        _log(sql=sql, ok=False, error=str(exc), origin=origin, session_id=session_id)
        raise
    result["caveats"] = caveats_for(sql)
    _log(
        sql=sql,
        ok=True,
        row_count=result["showing"],
        truncated=result["truncated"],
        elapsed_ms=result["elapsed_ms"],
        origin=origin,
        session_id=session_id,
        caveats=result["caveats"],
    )
    return result


def schema_overview() -> dict:
    """Every readable table with its row count, plus how to go deeper."""
    if not sql_query_enabled():
        raise ValidationError(DISABLED_MESSAGE)
    tables = sql_introspect.list_tables()
    return {
        "dialect": sql_console.dialect(),
        "tables": tables,
        "showing": len(tables),
        "total": str(len(tables)),
        "next": (
            "Call describe_db_schema(table='<name>') for one table's columns, indexes and — "
            "crucially — the keys inside its `payload` JSON column."
        ),
        "caveats": [PAYLOAD_CAVEAT],
    }


def schema_for_table(name: str) -> dict:
    if not sql_query_enabled():
        raise ValidationError(DISABLED_MESSAGE)
    described = sql_introspect.describe_table(name)
    described["caveats"] = caveats_for(f"{name} payload")
    return described


def recent_queries(limit: int = 50) -> list[dict]:
    """The owner-facing audit trail. Never read back into a model context."""
    return SqlQueryLogStore().recent(limit=limit)
