"""Render the spreadsheet formats an artifact can be: XLSX/CSV from a typed table spec.

PDF from HTML lives in ``artifact_pdf``.

Lucius writes text — an HTML page or a JSON table spec — and this module turns it into the file
a person opens in a PDF reader or a spreadsheet. Nothing here computes money: a table's totals
are live ``SUM`` formulas the spreadsheet evaluates, never figures this code adds up.

Untrusted input: the spec comes from a model that may have read a hostile merchant name, so
every text cell is written as TEXT — a value starting ``=``/``+``/``-``/``@`` never becomes a
formula (CSV/formula injection, OWASP).
"""

from __future__ import annotations

import csv
import io
import json
import re
import unicodedata
from datetime import date, datetime

from backend.core.errors import ValidationError

MAX_SHEETS = 20
MAX_COLUMNS = 60
MAX_ROWS = 50_000
MAX_CELLS = 200_000  # across every sheet — bounds the render before it starts
COLUMN_TYPES = ("text", "number", "integer", "money", "percent", "date")
_FORMULA_LEAD = ("=", "+", "-", "@", "\t", "\r", "\n")
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
_SHEET_NAME_BAD = re.compile(r"[\[\]:*?/\\]")


# ── Table spec ─────────────────────────────────────────────────────────────────


def parse_table_spec(content: str) -> dict:
    """Validate the JSON table spec Lucius sends for an xlsx/csv artifact.

    ``{"sheets": [{"name", "title"?, "columns": [{"header", "type", "currency"?}],
    "rows": [[...]], "totals"?: ["<header>", ...]}]}`` — raises ``ValidationError`` naming the
    exact sheet/row/column on any problem, so the model can correct it."""
    try:
        spec = json.loads(content)
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"xlsx/csv content must be a JSON table spec: {exc}") from exc
    sheets = spec.get("sheets") if isinstance(spec, dict) else None
    if not isinstance(sheets, list) or not sheets:
        raise ValidationError('table spec needs a non-empty "sheets" list')
    if len(sheets) > MAX_SHEETS:
        raise ValidationError(f"at most {MAX_SHEETS} sheets")
    cells = sum(
        len(sh.get("rows") or []) * max(len(sh.get("columns") or []), 1)
        for sh in sheets
        if isinstance(sh, dict) and isinstance(sh.get("rows"), list)
    )
    if cells > MAX_CELLS:
        raise ValidationError(f"at most {MAX_CELLS} cells in total — split the export")
    names: set[str] = set()
    return {"sheets": [_parse_sheet(sheet, i, names) for i, sheet in enumerate(sheets)]}


def _clean(text: object, limit: int) -> str:
    """Text a spreadsheet can store: control characters removed, length bounded."""
    return _CONTROL_CHARS.sub("", str(text))[:limit]


def _parse_sheet(sheet: object, index: int, names: set[str]) -> dict:
    where = f"sheet {index + 1}"
    if not isinstance(sheet, dict):
        raise ValidationError(f"{where} must be an object")
    name = _SHEET_NAME_BAD.sub(" ", _clean(sheet.get("name") or f"Sheet{index + 1}", 31)).strip()
    if not name or name.lower() in names:
        raise ValidationError(f"{where}: sheet names must be present and unique")
    names.add(name.lower())
    columns = sheet.get("columns")
    if not isinstance(columns, list) or not columns or len(columns) > MAX_COLUMNS:
        raise ValidationError(f"{where}: needs 1–{MAX_COLUMNS} columns")
    cols = [_parse_column(col, f"{where} column {j + 1}") for j, col in enumerate(columns)]
    rows = sheet.get("rows")
    if not isinstance(rows, list) or len(rows) > MAX_ROWS:
        raise ValidationError(f"{where}: rows must be a list of at most {MAX_ROWS}")
    parsed = [_parse_row(row, cols, f"{where} row {r + 1}") for r, row in enumerate(rows)]
    headers = [c["header"] for c in cols]
    totals = sheet.get("totals") or []
    if not isinstance(totals, list) or any(t not in headers for t in totals):
        raise ValidationError(f"{where}: totals must list column headers")
    for t in totals:
        if cols[headers.index(t)]["type"] not in ("number", "integer", "money"):
            raise ValidationError(f"{where}: total column {t!r} must be numeric")
    title = _clean(sheet.get("title") or "", 200).strip()
    return {"name": name, "title": title, "columns": cols, "rows": parsed, "totals": totals}


def _parse_column(col: object, where: str) -> dict:
    if not isinstance(col, dict) or not str(col.get("header") or "").strip():
        raise ValidationError(f"{where}: needs a header")
    kind = str(col.get("type") or "text")
    if kind not in COLUMN_TYPES:
        raise ValidationError(f"{where}: type must be one of {', '.join(COLUMN_TYPES)}")
    currency = str(col.get("currency") or "").strip().upper()[:5]
    if currency and not currency.isalpha():
        raise ValidationError(f"{where}: currency must be a code like EUR")
    return {"header": _clean(col["header"], 120).strip(), "type": kind, "currency": currency}


def _parse_row(row: object, cols: list[dict], where: str) -> list:
    if not isinstance(row, list) or len(row) != len(cols):
        raise ValidationError(f"{where}: needs exactly {len(cols)} cells")
    return [_parse_cell(v, c, f"{where}, {c['header']!r}") for v, c in zip(row, cols)]


def _parse_cell(value: object, col: dict, where: str):
    if value is None or value == "":
        return None
    kind = col["type"]
    if kind == "text":
        return _clean(value, 32_000)
    if kind == "date":
        try:
            return date.fromisoformat(str(value)[:10])
        except ValueError as exc:
            raise ValidationError(f"{where}: dates are YYYY-MM-DD") from exc
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValidationError(f"{where}: {kind} cells take a number, got {value!r}")
    return int(value) if kind == "integer" else float(value)


def _is_formula_like(text: str) -> bool:
    """Would a spreadsheet (or an importer that trims and normalises) read this as a formula?
    Leading spaces are ignored and look-alikes such as a full-width ＝ normalised (NFKC)."""
    if text.startswith(_FORMULA_LEAD):
        return True
    stripped = text.lstrip(" \u00a0\u3000")
    return bool(stripped) and unicodedata.normalize("NFKC", stripped[0]).startswith(_FORMULA_LEAD)


# ── XLSX ───────────────────────────────────────────────────────────────────────

_HEADER_FILL = "102A23"
_NUMBER_FORMATS = {
    "number": "#,##0.00",
    "integer": "#,##0",
    "percent": "0.0%",
    "date": "d mmm yyyy",
}


def _money_format(currency: str) -> str:
    unit = f'"{currency} "' if currency else ""
    return f"{unit}#,##0.00;[Red]-{unit}#,##0.00"


def render_xlsx(spec: dict, *, title: str) -> bytes:
    """A formatted workbook: bold frozen header, typed number formats, filters, live totals."""
    from openpyxl import Workbook
    from openpyxl.styles import Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    wb.remove(wb.active)
    wb.properties.title = title
    wb.properties.creator = "Open Net Worth"
    for sheet in spec["sheets"]:
        ws = wb.create_sheet(sheet["name"])
        cols, row_no = sheet["columns"], 1
        if sheet["title"]:
            _text_cell(ws, 1, 1, sheet["title"]).font = Font(bold=True, size=14)
            row_no = 3
        header_row = row_no
        for j, col in enumerate(cols, start=1):
            cell = _text_cell(ws, header_row, j, col["header"])
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = PatternFill("solid", fgColor=_HEADER_FILL)
        for values in sheet["rows"]:
            row_no += 1
            for j, (value, col) in enumerate(zip(values, cols), start=1):
                cell = ws.cell(row=row_no, column=j, value=value)
                if col["type"] == "text" and value is not None:
                    cell.data_type = "s"  # never a formula, whatever the text starts with
                elif col["type"] == "money":
                    cell.number_format = _money_format(col["currency"])
                elif col["type"] in _NUMBER_FORMATS:
                    cell.number_format = _NUMBER_FORMATS[col["type"]]
        last_data = row_no
        if sheet["totals"] and sheet["rows"]:
            row_no += 1
            _text_cell(ws, row_no, 1, "Total").font = Font(bold=True)
            top = Border(top=Side(style="thin"))
            for j, col in enumerate(cols, start=1):
                ws.cell(row=row_no, column=j).border = top
                if col["header"] in sheet["totals"]:
                    letter = get_column_letter(j)
                    cell = ws.cell(row=row_no, column=j)
                    cell.value = f"=SUM({letter}{header_row + 1}:{letter}{last_data})"
                    cell.font = Font(bold=True)
                    cell.number_format = (
                        _money_format(col["currency"])
                        if col["type"] == "money"
                        else _NUMBER_FORMATS[col["type"]]
                    )
        ws.freeze_panes = ws.cell(row=header_row + 1, column=1)
        if sheet["rows"]:
            ws.auto_filter.ref = f"A{header_row}:{get_column_letter(len(cols))}{last_data}"
        for j, col in enumerate(cols, start=1):
            longest = max(
                [len(col["header"])] + [len(_display(r[j - 1])) for r in sheet["rows"][:500]]
            )
            ws.column_dimensions[get_column_letter(j)].width = min(max(longest + 3, 10), 60)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _text_cell(ws, row: int, column: int, text: str):
    """Write a string that is always TEXT — openpyxl would otherwise store a value starting
    with ``=`` as a live formula. The one path every non-number string takes."""
    cell = ws.cell(row=row, column=column, value=text)
    cell.data_type = "s"
    return cell


def _display(value: object) -> str:
    if isinstance(value, float):
        return f"{value:,.2f}"
    return "" if value is None else str(value)


# ── CSV ────────────────────────────────────────────────────────────────────────


def render_csv(spec: dict) -> str:
    """The FIRST sheet as CSV (a CSV has one table). Text that a spreadsheet would run as a
    formula is prefixed with an apostrophe."""
    sheet = spec["sheets"][0]
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow([_safe_text(c["header"]) for c in sheet["columns"]])
    for row in sheet["rows"]:
        writer.writerow([_csv_cell(v) for v in row])
    return out.getvalue()


def _csv_cell(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, (int, float)):
        return repr(value) if isinstance(value, float) else str(value)
    return _safe_text(str(value))


def _safe_text(text: str) -> str:
    return f"'{text}" if _is_formula_like(text) else text
