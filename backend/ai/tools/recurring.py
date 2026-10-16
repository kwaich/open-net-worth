"""Recurring cash-flow stream tools (.claude/rfcs/2026-07-27-cashflow-stream-model.md).

The forward cash-flow projection no longer averages every income/expense transaction into one
flat monthly number (which extrapolated one-off proceeds forever). It classifies the ledger
into recurring STREAMS (projected on their own cadence), a VARIABLE residual (averaged), and
one-off items (excluded). These tools expose the two things a model can usefully do about
that: read the classification, and correct it.

Corrections are deliberately Lucius-reachable rather than UI-only (the AI-native design test):
"that wasn't recurring, ignore it" or "yes, that is my salary" should be sayable in chat.
Detection itself is never stored — only these confirm/dismiss corrections are — so a dismissal
survives new transactions arriving while the detection under it stays live. There is no tool
to declare a stream history cannot show: a future change is a Commitment or a simulate_cashflow
what-if (ADR 2026-10-16-declared-recurring-streams-are-retired).
"""

from __future__ import annotations

import json
import logging
from typing import Literal, Optional

from pydantic import Field

from backend.ai.tools.registry import tool
from backend.ai.tools.schema_from_model import ToolInput, schema_of
from backend.db.recurring_stream_store import RecurringStreamStore
from backend.services import ledger as ledger_service
from backend.services.fx_cache import load_fx_cache
from backend.services.recurring_baseline import resolve_baseline

logger = logging.getLogger(__name__)

_DEFAULT_LIMIT = 40


def _ok(payload: dict):
    """Handlers return (json_text, proposed_change) — never a bare dict."""
    return json.dumps({"ok": True, **payload}, ensure_ascii=False), None


def _err(message: str):
    return json.dumps({"error": message}, ensure_ascii=False), None


class GetRecurringStreamsInput(ToolInput):
    direction: Optional[Literal["in", "out"]] = Field(
        default=None, description="Only income ('in') or only expense ('out'). Omit for both."
    )
    include_candidates: Optional[bool] = Field(
        default=None,
        description=(
            "Include unconfirmed candidates — streams seen only twice, plus ones the user "
            "dismissed. These are NOT used in the projection. Default false."
        ),
    )
    limit: Optional[int] = Field(
        default=None, description=f"Max streams to return (default {_DEFAULT_LIMIT})."
    )
    offset: Optional[int] = Field(default=None, description="Skip this many streams (paging).")


@tool(
    "get_recurring_streams",
    description=(
        "Read the recurring cash-flow streams behind the forward projection: salary, rent, "
        "subscriptions, loan repayments — each with its cadence (monthly/weekly/quarterly/"
        "annual), amount, occurrence count and confidence. Also returns the VARIABLE baseline "
        "(irregular everyday spending, averaged) and the one-off transactions EXCLUDED from "
        "the forecast (a stock sale, a one-time repair) so you can explain why a projection "
        "does not simply extrapolate last month. Use for 'what are my recurring bills', 'what "
        "is my real monthly income', 'why does my cash forecast look like that'."
    ),
    input_schema=schema_of(GetRecurringStreamsInput, tool_name="get_recurring_streams"),
)
async def get_recurring_streams(input_data: dict):
    base = ledger_service.base_currency()
    result = resolve_baseline(base, load_fx_cache())
    direction = input_data.get("direction")
    limit = int(input_data.get("limit") or _DEFAULT_LIMIT)
    offset = max(int(input_data.get("offset") or 0), 0)

    streams = result["streams"]
    if direction:
        streams = [s for s in streams if s.get("direction") == direction]
    total = len(streams)
    page = streams[offset : offset + limit]

    out = {
        "base_currency": base,
        "streams": [_public(s) for s in page],
        "showing": len(page),
        "total": total,
        "variable_income_per_month": result["variable_in"],
        "variable_expenses_per_month": result["variable_out"],
        "excluded_one_offs": result["one_offs"][:10],
        "excluded_one_offs_total": len(result["one_offs"]),
        "detection_window": result["detection_window"],
    }
    if total > offset + len(page):
        out["note"] = (
            f"Showing {len(page)} of {total} streams. Use offset={offset + len(page)} for more."
        )
    if not result["streams"]:
        out["note"] = (
            "No recurring streams detected yet — a stream needs 3 occurrences at a regular "
            "interval. The projection is using the variable averages above. This is normal for "
            "a new book."
        )
    if input_data.get("include_candidates"):
        out["candidates"] = [_public(s) for s in result["candidates"][:limit]]
        out["candidates_note"] = (
            "Candidates are NOT in the projection: seen only twice (not yet proven) or "
            "dismissed by the user. set_recurring_status(status='confirm') promotes one."
        )
    return json.dumps(out, ensure_ascii=False), None


def _public(stream: dict) -> dict:
    """Drop the internal txn_ids list — it is large, unstable, and useless to the model."""
    return {k: v for k, v in stream.items() if k != "txn_ids"}


class SetRecurringStatusInput(ToolInput):
    key: str = Field(description="The stream's `key` from get_recurring_streams.")
    status: Literal["confirm", "dismiss", "reset"] = Field(
        description=(
            "'confirm' — treat as recurring and USE it in the projection (promotes a "
            "twice-seen candidate). 'dismiss' — never treat as recurring. 'reset' — drop the "
            "override and go back to automatic detection."
        )
    )
    note: Optional[str] = Field(default=None, description="Optional reason, stored with it.")


@tool(
    "set_recurring_status",
    description=(
        "Confirm or dismiss a DETECTED recurring stream, correcting the automatic detection. "
        "Confirm a real bill/salary seen only twice so the forecast starts counting it; "
        "dismiss something that repeated by coincidence. Changes what the cash-flow "
        "projection assumes, so state the effect back to the user."
    ),
    input_schema=schema_of(SetRecurringStatusInput, tool_name="set_recurring_status"),
    mutates=("plan",),
)
async def set_recurring_status(input_data: dict):
    key = str(input_data.get("key") or "").strip()
    action = str(input_data.get("status") or "").strip().lower()
    if not key:
        return _err("key is required — get it from get_recurring_streams.")
    store = RecurringStreamStore()
    if action == "reset":
        return _ok({"key": key, "status": "detected", "reset": store.reset(key)})
    try:
        row = store.set_status(key, {"confirm": "confirmed", "dismiss": "dismissed"}[action])
    except (KeyError, ValueError) as exc:
        return _err(str(exc) or f"status must be confirm, dismiss or reset (got {action})")
    return _ok({"key": key, "status": row["status"]})
