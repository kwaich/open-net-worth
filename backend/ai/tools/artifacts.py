"""Chat-artifact tool (inline HTML/SVG visuals).

``create_artifact`` lets Lucius render a self-contained document — a chart
dashboard, a spending breakdown, a rich card list — inline in the chat (inside a
null-origin sandboxed iframe) with a download-as-file affordance.

The handler stores the bytes via ``services.artifacts`` and returns ONLY a compact
reference (id/title/filename/type/size) — the document never round-trips through
the model as a tool result. Headless-allowed: agents and scheduled runs may
persist artifacts; rendering is a chat-UI concern.
"""

from __future__ import annotations

import asyncio
import json
from typing import Literal, Optional, Tuple

from pydantic import Field

from backend.ai.tools.registry import tool
from backend.ai.tools.schema_from_model import ToolInput, schema_of
from backend.core.errors import OpenNetWorthError
from backend.services import artifacts as artifacts_service


class CreateArtifactInput(ToolInput):
    """Schema-of-record for ``create_artifact`` (see ``schema_from_model``).

    ``title`` is a REAL parameter here, not the ``title`` keyword Pydantic injects per
    field — the exact hazard ``schema_from_model``'s structure-aware walk exists to guard
    against (see its module docstring).
    """

    title: str = Field(
        description="Short human title shown on the artifact card, e.g. 'May 2026 Spending Overview'."
    )
    content: Optional[str] = Field(
        default=None,
        description=(
            "html/svg: the COMPLETE self-contained document. pdf: an HTML page to lay out on "
            "A4 (omit it when converting with source_artifact_id). xlsx/csv: a JSON table "
            'spec — {"sheets": [{"name": "Transactions", "title": "optional heading", '
            '"columns": [{"header": "Date", "type": "date"}, {"header": "Amount", "type": '
            '"money", "currency": "<code from the tools>"}], "rows": [["2026-09-01", 2750.0]], '
            '"totals": ["Amount"]}]}. Column types: text, number, integer, money, percent '
            "(a fraction, 0.12 = 12%), date (YYYY-MM-DD). totals become live SUM formulas — "
            "never add numbers up yourself. csv writes the first sheet only."
        ),
    )
    artifact_type: Optional[Literal["html", "svg", "pdf", "xlsx", "csv"]] = Field(
        default=None,
        description=(
            "html (default) and svg render in the chat. pdf, xlsx and csv are FILES the user "
            "opens or downloads: pdf for a statement to keep or print, xlsx for figures to "
            "work with in Excel/Google Sheets/Numbers, csv for import into another tool."
        ),
    )
    source_artifact_id: Optional[str] = Field(
        default=None,
        description=(
            "pdf only: the artifact_id of an html artifact you created, to produce its PDF "
            "without writing the page again."
        ),
    )
    filename: Optional[str] = Field(
        default=None,
        description="Download filename, e.g. may_2026_spending.html. Derived from the title when omitted.",
    )


@tool(
    "create_artifact",
    description=(
        "Render a visual artifact inline in the chat — use when the "
        "user wants to SEE data: charts, dashboards, reports, lists. ONE "
        "self-contained document: inline CSS/JS, inline SVG preferred, vanilla "
        "JS or <canvas>. NO network (no CDN, no fetch): embed every data value. "
        "DATA FIDELITY: copy values VERBATIM from this conversation's tool results "
        "(never memory or re-rounded); show a derived number (total, %, delta) "
        "only if a tool computed it or the document's JS computes it from the "
        "embedded rows; label metric (value vs cost basis vs net worth) and currency "
        "as the tool results do. LAYOUT: a NARROW chat column (~340-480px) "
        "— mobile-first, ONE column, chart above its "
        "list, responsive SVG (viewBox + width:100%), body{margin:0}, natural "
        "height (never 100vh or inner scroll), render ALL rows. Dark: bg "
        "#0f1115, text #e8eaed, muted #9aa0a6, system-ui, tabular numerals. One "
        "caption max. 2 MB, 3 per turn. FILES: artifact_type pdf/xlsx/csv make a download (see "
        "content); lay a PDF out with TABLES (no flex/grid). CHARTS THAT SURVIVE PDF AND "
        'TELEGRAM: put <script type="application/x-onw-chart">{json}</script> where a '
        "chart goes; the server draws static SVG from your data. Types: line "
        '({"type":"line","format":"percent","points":[{"date":..., "a":0.01}],'
        '"series":{"a":"Label"}} — get_returns\' series pastes in as points), stack '
        '({"type":"stack","format":"percent","items":[{"label","value","target"}]}), '
        'diverging ({"type":"diverging","format":"money","currency":...,"items":'
        '[{"label","value","note"}]}).'
    ),
    input_schema=schema_of(CreateArtifactInput, tool_name="create_artifact"),
    mutates=("artifacts",),
)
async def create_artifact(input_data: dict) -> Tuple[str, Optional[dict]]:
    try:
        # pdf/xlsx rendering is CPU work; keep it off the event loop (to_thread copies the
        # context, so the per-turn artifact scope and its cap still apply).
        record = await asyncio.to_thread(
            artifacts_service.create_artifact,
            title=input_data.get("title", ""),
            content=input_data.get("content"),
            artifact_type=input_data.get("artifact_type") or "html",
            filename=input_data.get("filename"),
            source_artifact_id=input_data.get("source_artifact_id"),
        )
    except OpenNetWorthError as exc:
        return json.dumps({"error": exc.message or str(exc)}), None
    return (
        json.dumps(
            {
                "artifact_id": record["id"],
                "title": record["title"],
                "filename": record["filename"],
                "artifact_type": record["artifact_type"],
                "size_bytes": record["size_bytes"],
                "status": (
                    "rendered inline in the chat with a download button"
                    if record["artifact_type"] in artifacts_service.RENDERABLE_TYPES
                    else "shown in the chat as a file the user can open or download"
                ),
            }
        ),
        None,
    )
