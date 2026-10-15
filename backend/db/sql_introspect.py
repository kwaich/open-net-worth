"""Live introspection of the DATABASE's own shape — the read side of ``describe_db_schema``.

Split out of ``sql_console.py`` (which kept execution + enforcement) because the two answer
different questions and neither needs the other's internals: this module asks *what is in
there*, that one asks *may this statement run*. It consumes ``sql_console``'s public surface —
``execute_readonly``'s guarantees apply to every read here too, so introspection can never
become a back door around the table denylist.

## Why this exists at all

Most stores in this app are a single ``payload`` JSON column (``payload_table()`` /
``book_payload_table()``). So the obvious ``SELECT quantity FROM positions`` ERRORS, and the
real query is ``json_extract(payload, '$.quantity')``. A model cannot know that. Without
``payload_keys`` below, every query ``run_sql_query`` receives is a guess against columns that
do not exist.

Introspecting live (rather than baking a schema into the tool description) means it cannot go
stale after a migration, and costs nothing on turns that never ask.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy import inspect

from backend.core.errors import ValidationError
from backend.db import engine as eng
from backend.db.sql_console import (
    _fetch,
    assert_tables_allowed,
    denied_tables,
    execute_readonly,
)

logger = logging.getLogger(__name__)

_SAMPLE_ROWS_FOR_PAYLOAD_KEYS = 25
# See sql_console._cell(max_chars=…): the model-context cap exists to bound what a MODEL reads.
# Only derived key NAMES leave this module, so applying it here would do nothing except break
# json.loads mid-object — which it did, until this constant existed.
_UNBOUNDED_CELL = 10**9

_DENIED_MESSAGE = (
    "Refused: {name} is not readable through this tool. Credential tables are never exposed, "
    "and chat transcripts have their own tools."
)

# Columns whose storage type is a trap for anyone writing SQL against them.
_MONEY_TEXT_COLUMNS = frozenset(
    {"from_amount", "to_amount", "base_amount", "fx_rate", "original_amount", "quantity", "amount"}
)


def readable_tables() -> list[str]:
    """Every table this tool may read, alphabetically. Denied tables are not merely
    unreadable — they are not advertised either."""
    inspector = inspect(eng.get_engine())
    return sorted(n for n in inspector.get_table_names() if n.lower() not in denied_tables())


def list_tables() -> list[dict]:
    """Table names with row counts, so the model can tell an empty store from a full one."""
    out: list[dict] = []
    for name in readable_tables():
        try:
            counted = execute_readonly(f'SELECT COUNT(*) FROM "{name}"', limit=1)
            rows = int(counted["rows"][0][0]) if counted["rows"] else 0
        except Exception:  # noqa: BLE001 — one unreadable table must not sink the whole map
            logger.warning("sql_console: could not count %s", name, exc_info=True)
            rows = -1
        out.append({"table": name, "rows": rows})
    return out


def describe_table(name: str) -> dict:
    """Columns, indexes and — for a payload-shaped store — the observed payload keys.

    ``payload_keys`` is the reason this function exists. Most stores in this app are a single
    ``payload`` JSON column (``payload_table()`` / ``book_payload_table()``), so the obvious
    ``SELECT quantity FROM positions`` errors and the real query is
    ``json_extract(payload, '$.quantity')``. A model cannot know that without being told.
    """
    if name.lower() in denied_tables():
        raise ValidationError(_DENIED_MESSAGE.format(name=name))
    inspector = inspect(eng.get_engine())
    if name not in inspector.get_table_names():
        raise ValidationError(
            f"No table named {name!r}. Call describe_db_schema() with no argument to list them."
        )
    columns = [
        {
            "name": c["name"],
            "type": str(c["type"]),
            "nullable": bool(c.get("nullable", True)),
            "primary_key": c["name"] in _pk_names(inspector, name),
        }
        for c in inspector.get_columns(name)
    ]
    for column in columns:
        note = _column_note(name, column["name"])
        if note:
            column["note"] = note
    return {
        "table": name,
        "columns": columns,
        "indexes": sorted(i["name"] or "" for i in inspector.get_indexes(name)),
        "payload_keys": _payload_keys(name, columns),
    }


def _pk_names(inspector, name: str) -> set[str]:
    return set(inspector.get_pk_constraint(name).get("constrained_columns") or [])


def _column_note(table: str, column: str) -> str | None:
    if column in _MONEY_TEXT_COLUMNS:
        return "exact decimal stored as TEXT — never CAST to float for comparison"
    if column in {"payload", "data"}:
        return "verbatim row, the round-trip source of truth; query with json_extract()"
    if table == "transactions" and column == "deleted":
        return "1 = soft-deleted; this table retains deleted rows"
    if table == "transactions" and column == "merged_into":
        return "non-null = merged away into another row; exclude it from counts"
    return None


def _payload_keys(name: str, columns: list[dict]) -> list[str]:
    """Top-level keys observed across a sample of rows. Empty for a table with no payload."""
    json_column = next((c["name"] for c in columns if c["name"] in {"payload", "data"}), None)
    if json_column is None:
        return []
    statement = f'SELECT {json_column} FROM "{name}" LIMIT {_SAMPLE_ROWS_FOR_PAYLOAD_KEYS}'
    try:
        assert_tables_allowed(statement)
        # Un-truncated on purpose: only the derived key NAMES leave this function, so the
        # model-context cap would do nothing here except break json.loads mid-object.
        _cols, rows, _trunc, _clipped = _fetch(
            statement, _SAMPLE_ROWS_FOR_PAYLOAD_KEYS, _UNBOUNDED_CELL
        )
    except Exception:  # noqa: BLE001 — a sample failure must not sink the description
        logger.warning("sql_console: could not sample payload of %s", name, exc_info=True)
        return []
    keys: set[str] = set()
    for row in rows:
        keys |= _keys_of(row[0] if row else None)
    return sorted(keys)


def _keys_of(payload: Any) -> set[str]:
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except ValueError:
            return set()
    return set(payload.keys()) if isinstance(payload, dict) else set()
