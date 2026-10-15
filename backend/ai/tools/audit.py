"""Transaction provenance read tool (transaction-provenance RFC, Phase 2).

``trace_transactions`` answers "where did this transaction come from, and what has
happened to it since?" — the create/update/delete history with WHO did it (user/ai/
agent/system), through WHAT surface (ui / chat:web / chat:telegram / mcp / agent:* /
recurring / import), the linked chat session + the user's request that drove it, and a
before/after diff on edits. Read-only, headless-allowed; there is NO write counterpart
(provenance is recorded by the system, never authored by a model).

Recorded events win; for a row that predates the feature it falls back to a best-effort
reconstruction from the row's own fields (flagged ``reconstructed: true``).

Renamed from ``trace_transaction`` (harness reliability program, 2026-07-19 owner
complaint about singular tool names secretly accepting a list — see ``accounts.py``'s
module docstring for the full rationale). The batch param is now a plain ``transaction_ids``
ARRAY, not an ``anyOf[string, array]`` on a singular name: ``trace_transactions`` returns a
FULL event history per id (heavier than a bare id/flag), so there is nothing to gain from a
scalar shorthand here. The old name still resolves via alias.
"""

from __future__ import annotations

import json
from typing import List

from pydantic import Field

from backend.ai.tools.registry import tool
from backend.ai.tools.schema_from_model import ToolInput, schema_of
from backend.services import audit as audit_service

# A generous ceiling on an explicit id list: trace_transactions returns a full event
# history per id (heavier than a bare id/flag), so a batch this large is refused loudly
# (split into multiple calls) rather than silently truncated — truncating an EXPLICIT id
# list would hide the exact row the caller named, which the bounded-tool-result rule
# (CLAUDE.md) says never to do.
_MAX_BATCH_TRACE_IDS = 25


def _trace_ids_from(input_data: dict) -> list[str]:
    """``transaction_ids`` if present, else the legacy scalar-or-list ``transaction_id`` an
    old (pre-rename) alias call would use. Blanks dropped, order preserved."""
    raw = input_data.get("transaction_ids")
    if raw is None:
        raw = input_data.get("transaction_id")
    if isinstance(raw, list):
        return [str(t).strip() for t in raw if str(t or "").strip()]
    ref = str(raw or "").strip()
    return [ref] if ref else []


class TraceTransactionsInput(ToolInput):
    """Schema-of-record for ``trace_transactions`` (see ``schema_from_model``)."""

    transaction_ids: List[str] = Field(
        min_length=1,
        description=(
            f"The ids of the transactions to trace (up to {_MAX_BATCH_TRACE_IDS} per call, "
            "e.g. every row from one import batch). Pass one id to trace a single "
            "transaction — it returns the same single-object shape as before; several "
            "return {results: [...]}."
        ),
    )


@tool(
    "trace_transactions",
    description=(
        "Trace the provenance / audit history of one or more transactions: how each was "
        "created, and every edit or deletion since. Returns an ordered ``events`` list per "
        "transaction — each event has ``op`` (create|update|delete|restore), ``ts``, "
        "``actor`` (user|ai|agent|system), ``surface`` (ui|chat:web|chat:telegram|"
        "chat:slack|chat:discord|chat:whatsapp|mcp|agent:<id>|recurring:<task_id>|"
        "import:<...>), ``session_id`` (the chat it came from), ``intent`` (the user's "
        "request that drove it), and on an edit a sparse ``before``/``after`` field diff. "
        "``reconstructed: true`` means no audit was recorded for this row (it predates "
        "provenance) so the result is a best-effort guess from the row's own fields. Use "
        "to answer 'where did this transaction come from / who added or changed it / why "
        "is this date wrong'. Read-only.\n\n"
        "Pass several transaction_ids (up to 25) to trace many transactions in one call "
        "instead of one call per id."
    ),
    input_schema=schema_of(TraceTransactionsInput, tool_name="trace_transactions"),
)
async def trace_transactions(input_data: dict):
    ids = _trace_ids_from(input_data)

    if not ids:
        return json.dumps({"error": "transaction_ids is required"}), None
    if len(ids) > _MAX_BATCH_TRACE_IDS:
        return json.dumps(
            {
                "error": (
                    f"{len(ids)} transaction_ids exceeds the {_MAX_BATCH_TRACE_IDS}-id cap "
                    "per call — split into multiple calls."
                )
            }
        ), None

    # A lone id takes the single-transaction path — the exact pre-rename response shape.
    if len(ids) == 1:
        result = audit_service.trace(ids[0])
        return json.dumps(result, ensure_ascii=False), None

    results = [audit_service.trace(tid) for tid in ids]
    return json.dumps({"results": results, "count": len(results)}, ensure_ascii=False), None
