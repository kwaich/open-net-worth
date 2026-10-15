"""Report-library tool (P4-RP1).

Lucius can list the report catalog and fetch a generated report's structured data:

  * ``get_report`` — read / compute (proposed_change = None). Returns the structured report
    model (the same payload the JSON API serves) for a given report type + period. A report is
    a COMPOSITION of the existing read services (net worth, returns, realized P&L, income,
    spending) — no money math here; everything is delegated to ``services.reports``.

This is a pure read (no write, no money mutation), so it is on the headless/agent allowlist.
Respects the dependency rule (ai -> services -> db -> core): only marshals I/O.
"""

from __future__ import annotations

import json
from typing import Literal, Optional

from pydantic import Field

from backend.ai.tools.registry import tool
from backend.ai.tools.schema_from_model import ToolInput, schema_of
from backend.services import reports as reports_service

_REPORT_TYPES = tuple(r["type"] for r in reports_service.REPORT_TYPES)


class GetReportInput(ToolInput):
    """Schema-of-record for ``get_report`` (see ``schema_from_model``)."""

    # The report catalog is data-driven, so the enum is built from the same
    # ``_REPORT_TYPES`` source the literal schema used (list(_REPORT_TYPES)) rather than a
    # hand-written Literal — ``Literal[a_tuple]`` is equivalent to spelling out each member.
    # mypy can't statically resolve a Literal built from a runtime tuple (valid-type) — a
    # real typing.Literal at runtime, verified by the schema-derivation golden test.
    type: Literal[_REPORT_TYPES] = Field(description="Which report to generate.")  # type: ignore[valid-type]
    period: Optional[Literal["1M", "3M", "YTD", "1Y", "ALL"]] = Field(
        default=None,
        description="Window for period-driven reports (ignored by spending_summary).",
    )


@tool(
    "get_report",
    description=(
        "Generate a structured financial report (read-only). Report types: "
        "'net_worth_statement' (balances + positions + period return), "
        "'realized_income' (gains + dividend/interest by instrument/term/kind, with yield-on-cost), "
        "'spending_summary' (current month expenses vs budget by category/account), "
        "'cash_flow_statement' (income vs expense by category + savings rate), "
        "'budget_vs_actual' (Planning categories: assigned vs activity vs available). "
        "Pass type + period (1M|3M|YTD|1Y|ALL, default 1Y; ignored by spending_summary). "
        "HTML for printing is available from Settings → Data."
    ),
    input_schema=schema_of(GetReportInput, tool_name="get_report"),
)
async def get_report(input_data: dict):
    report_type = (input_data.get("type") or "").strip()
    period = (input_data.get("period") or "1Y").strip()
    try:
        report = reports_service.build_report(report_type, period)
    except ValueError as exc:
        return json.dumps({"error": str(exc), "available": list(_REPORT_TYPES)}), None
    # Return the structured model only (not the HTML) — the LLM reasons over the data; the HTML
    # is for the human download path.
    return json.dumps(report, ensure_ascii=False, default=str), None
