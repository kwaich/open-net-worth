"""Mapped CSV transaction import (the cheap, universal data-import path).

Steal the idea from Actual Budget / OpenClaw importers: the user uploads ANY CSV
(no per-bank format), tells us which columns map to which fields, and we build
two-leg transaction drafts and feed them through the ONE ledger choke point
``ledger.bulk_create_transactions`` — so the spend-rules engine + categorization +
base-currency math all apply on this path with no duplication.

Layer: services → db → core. No FastAPI/HTTP types here.

Idempotency: every row gets a stable ``external_source="csv"`` + ``external_ref="<sha256-of-row>"``
derived from the mapped fields. A re-import of the same CSV produces the same ids, so the
already-present rows are skipped (zero duplicates). Money/base figures are NEVER
fabricated here — the ledger recomputes the base from the native leg.

The single signed-``amount`` convention follows the common bank-feed sign convention:
positive = money IN (income), negative = money OUT (expense). For banks that split
debits/credits into two columns, map ``inflow`` + ``outflow`` instead (each a
non-negative magnitude).
"""

from __future__ import annotations

import csv
import hashlib
import io
import logging
import re
import unicodedata

from backend.services import ledger as ledger_service
from backend.services.categories import all_category_rows, resolve_category_ref
from backend.services.import_common import (
    PREVIEW_LIMIT,  # noqa: F401  (re-exported for callers/tests)
    ImportError_,
    PreviewResult,
    RowResult,
    build_commit,
    build_preview,
    disambiguate_identical_rows,
    flag_duplicates,
)

logger = logging.getLogger(__name__)

# The mapping keys the UI/API may supply. ``amount`` is mutually exclusive with the
# ``inflow``/``outflow`` pair; ``account_id`` (a chosen account) takes precedence over
# the ``account`` column (resolved by name).
_MAPPABLE_FIELDS = (
    "date",
    "description",
    "amount",
    "inflow",
    "outflow",
    "account",
    "category",
    "status",
)

# Auto-detect synonym lists — each tuple is (field_key, [synonyms...]).  The first synonym
# that matches a column (normalized, unique-substring) is used as the auto-detected column.
# English-only by design (this is an open-source project, not tuned to any one locale) — a
# column in any other language just falls through to manual mapping or the LLM-suggest
# fallback below, which has no language bias.
_AUTO_DETECT_SYNONYMS: list[tuple[str, list[str]]] = [
    (
        "date",
        [
            "date",
            "day",
            "txn date",
            "tnx date",
            "posted",
            "transaction date",
            "value date",
        ],
    ),
    (
        "description",
        [
            "description",
            "memo",
            "narrative",
            "details",
            "transactions in detail",
        ],
    ),
    ("outflow", ["debit", "withdrawal"]),
    ("inflow", ["credit", "deposit"]),
    ("amount", ["amount", "sum"]),
    ("balance", ["balance"]),
    ("status", ["status", "state", "result"]),
]


class CsvImportError(ImportError_):
    """A user-correctable problem with the upload or the mapping."""


# ── parsing ────────────────────────────────────────────────────────────────────────


def parse_csv(content: bytes) -> tuple[list[str], list[dict]]:
    """Decode + parse raw CSV bytes into (columns, rows). Raises CsvImportError on an
    empty file or one with no detectable header row."""
    text = content.decode("utf-8-sig", errors="replace")
    if not text.strip():
        raise CsvImportError("The file is empty.")
    try:
        reader = csv.DictReader(io.StringIO(text))
        columns = list(reader.fieldnames or [])
        rows = list(reader)
    except csv.Error as exc:
        raise CsvImportError(f"Could not parse the CSV: {exc}")
    columns = [c.strip() for c in columns if c is not None]
    if not columns:
        raise CsvImportError("No header row found — the first line must name the columns.")
    return columns, rows


def detect_headers(content: bytes) -> list[str]:
    """Just the column names, for the UI's mapping form (header auto-detect)."""
    columns, _ = parse_csv(content)
    return columns


# ── amount / date helpers ────────────────────────────────────────────────────────


# Matches the first date-like token (dd/mm/yyyy or similar) at the start of a cell that may
# contain extra text after a newline (e.g. "01/06/2026\n5378 - 66656").
_DATE_TOKEN_RE = re.compile(
    r"(\d{1,2}[/\-\.]\d{1,2}[/\-\.]\d{2,4}|\d{4}[/\-\.]\d{1,2}[/\-\.]\d{1,2})"
)

# Date format: dd/mm/yyyy or d/m/yyyy (VCB and many Asian banks).
_DMY_RE = re.compile(r"^(\d{1,2})[/\-\.](\d{1,2})[/\-\.](\d{4})$")


def _extract_date(raw: str) -> str:
    """Extract and normalize the first date-like token from a raw cell value.

    Handles cells that pack extra content after a newline (e.g. "01/06/2026\\n5378 - 66656").
    Converts dd/mm/yyyy → yyyy-mm-dd so the ledger sees a clean ISO date. If the value is
    already ISO (yyyy-mm-dd) or contains no recognizable date, returns the value unchanged
    (the existing date parsing in _row_to_draft handles other formats)."""
    if not raw:
        return raw
    # Try the first token that looks like a date.
    m = _DATE_TOKEN_RE.search(raw)
    if not m:
        return raw
    token = m.group(1)
    dmy = _DMY_RE.match(token)
    if dmy:
        d, mo, y = dmy.group(1), dmy.group(2), dmy.group(3)
        return f"{y}-{mo.zfill(2)}-{d.zfill(2)}"
    return token


# Amount cleaning: strip everything except digits, '.', '-', 'e', 'E'.
# We handle comma-thousands separately before this step.
_AMOUNT_FINAL_CLEAN = re.compile(r"[^0-9eE.\-]")


def _parse_amount(raw: str | None) -> float | None:
    """Parse a currency-ish amount string → float.

    Handles:
    - Comma thousands separators: "1,515,700" → 1515700
    - US style "1,234.56" → 1234.56
    - European style "1.234,56" → 1234.56 (last separator is decimal)
    - Dollar prefix, spaces, parenthesised negatives "(123.45)" → -123.45

    Returns None if unparseable."""
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    negative = s.startswith("(") and s.endswith(")")
    if negative:
        s = s[1:-1]
    # Strip leading currency symbols and spaces.
    s = s.lstrip("$€£¥₫₩₹ ")

    if "," in s and "." in s:
        # Both separators present — last one is the decimal.
        if s.rfind(",") > s.rfind("."):
            # European: "1.234,56"
            s = s.replace(".", "").replace(",", ".")
        else:
            # US: "1,234.56"
            s = s.replace(",", "")
    elif "," in s:
        # Only commas — could be thousands ("1,515,700") or European decimal ("1,50").
        # Distinguish: if there are multiple comma-separated groups and the last group
        # has exactly 3 digits, it's thousands-grouping.  Otherwise treat as decimal.
        parts = s.split(",")
        if len(parts) > 2 or (len(parts) == 2 and len(parts[-1]) == 3):
            # Thousands separator: "1,515,700" or "1,234" (ambiguous but treat as thousands)
            s = s.replace(",", "")
        else:
            # Decimal comma: "1,50"
            s = s.replace(",", ".")

    s = _AMOUNT_FINAL_CLEAN.sub("", s)
    if s in ("", "-", ".", "-."):
        return None
    try:
        val = float(s)
    except ValueError:
        return None
    return -val if negative else val


def _clean(raw: str | None) -> str:
    return (raw or "").strip()


# ── normalized column matching ───────────────────────────────────────────────────


def _normalize_col(label: str) -> str:
    """Normalize a column label for fuzzy matching: NFKD decompose, strip combining marks,
    casefold, collapse whitespace/newlines/'/' runs to single spaces, strip edges.

    This is the shared normalization used by both mapping validation and auto-detect, so
    newline-embedded headers like "Số tiền ghi nợ/\\nDebit" normalize to
    "so tien ghi no debit" and match a simple "Debit" mapping."""
    nfkd = unicodedata.normalize("NFKD", label)
    stripped = "".join(c for c in nfkd if unicodedata.category(c) != "Mn")
    casefolded = stripped.casefold()
    collapsed = re.sub(r"[\s/]+", " ", casefolded).strip()
    return collapsed


def _find_column(value: str, columns: list[str]) -> str | None:
    """Resolve a user-supplied mapping value to one of the actual column names.

    Resolution order (first match wins):
    1. Exact raw match (back-compat — plain ASCII headers always land here).
    2. Normalized equality — after NFKD + casefold + whitespace collapse.
    3. Unique substring — if the normalized value is contained in exactly ONE
       normalized column name (or vice versa). Returns None when zero or >1 columns match
       (the caller raises a helpful error listing the ambiguous candidates).

    Empty values and empty column names are never matched (the empty string is a
    substring of every string — skip both to prevent spurious matches)."""
    if not value:
        return None
    if value in columns:
        return value

    norm_value = _normalize_col(value)
    if not norm_value:
        return None
    norm_columns = [_normalize_col(c) for c in columns]

    # Normalized equality.
    for col, nc in zip(columns, norm_columns):
        if nc and nc == norm_value:
            return col

    # Unique substring containment (bidirectional) — only non-empty columns.
    matches = [
        col
        for col, nc in zip(columns, norm_columns)
        if nc and (norm_value in nc or nc in norm_value)
    ]
    if len(matches) == 1:
        return matches[0]
    # Multiple substring matches — caller handles the ambiguity error.
    return None


def _col_list_repr(columns: list[str], cap: int = 25) -> str:
    """Human-readable column list for error messages — newlines shown as \\n, capped."""
    shown = columns[:cap]
    parts = [repr(c.replace("\n", "\\n")) for c in shown]
    suffix = f", … ({len(columns) - cap} more)" if len(columns) > cap else ""
    return "[" + ", ".join(parts) + suffix + "]"


def _first_data_row_preview(rows: list[dict], columns: list[str]) -> str:
    """One-line preview of the first data row's values, for 'missing field' error messages."""
    if not rows:
        return "(no data rows)"
    row = rows[0]
    parts = [f"{c!r}: {str(row.get(c, ''))[:20]!r}" for c in columns[:6]]
    suffix = ", …" if len(columns) > 6 else ""
    return "{" + ", ".join(parts) + suffix + "}"


# ── mapping validation ───────────────────────────────────────────────────────────


def validate_mapping(columns: list[str], mapping: dict, rows: list[dict] | None = None) -> dict:
    """Validate a column→field mapping against the CSV's columns. Returns the cleaned
    mapping (only known fields, blanks dropped). Raises CsvImportError on a missing
    required field, an amount-mode conflict, or a column name not present in the file.

    Uses normalized matching so "Debit" finds "Số tiền ghi nợ/\\nDebit" and vice versa.
    Error messages include the available column list and (for missing-field errors) a
    one-line preview of the first data row so the caller can map without guessing."""
    col_list = _col_list_repr(columns)
    cleaned: dict = {}
    for field_name in _MAPPABLE_FIELDS:
        col = _clean(mapping.get(field_name))
        if not col:
            continue
        resolved = _find_column(col, columns)
        if resolved is None:
            # Check if ambiguous (multiple substring matches).
            norm_col = _normalize_col(col)
            norm_columns = [_normalize_col(c) for c in columns]
            ambiguous = [
                c for c, nc in zip(columns, norm_columns) if norm_col in nc or nc in norm_col
            ]
            if len(ambiguous) > 1:
                raise CsvImportError(
                    f"Mapped column '{col}' is ambiguous — matches multiple columns: "
                    f"{_col_list_repr(ambiguous)}. Available columns: {col_list}"
                )
            raise CsvImportError(
                f"Mapped column '{col}' is not in the file. Available columns: {col_list}"
            )
        cleaned[field_name] = resolved

    row_preview = _first_data_row_preview(rows or [], columns) if rows else ""
    hint = f" Available columns: {col_list}" + (
        f"; first data row: {row_preview}" if row_preview else ""
    )

    if "date" not in cleaned:
        raise CsvImportError(f"Map a column to 'date'.{hint}")
    if "description" not in cleaned:
        raise CsvImportError(f"Map a column to 'description'.{hint}")

    has_amount = "amount" in cleaned
    has_split = "inflow" in cleaned or "outflow" in cleaned
    if has_amount and has_split:
        raise CsvImportError(
            "Use EITHER a single 'amount' column OR 'inflow'/'outflow' — not both."
        )
    if not has_amount and not has_split:
        raise CsvImportError(f"Map a column to 'amount' (or to 'inflow'/'outflow').{hint}")

    return cleaned


def auto_detect_mapping(columns: list[str]) -> dict:
    """Attempt to auto-detect a column→field mapping from known synonyms.

    Returns a partial mapping dict (only fields that could be resolved). The caller should
    fall back to requiring explicit user mapping for any field not present in the result."""
    # Skip empty column names — the empty string matches everything as a substring.
    norm_columns = [(_normalize_col(c), c) for c in columns if c.strip()]
    result: dict = {}

    for field_key, synonyms in _AUTO_DETECT_SYNONYMS:
        if field_key in result:
            continue
        for synonym in synonyms:
            norm_syn = _normalize_col(synonym)
            if not norm_syn:
                continue
            # Exact normalized match first.
            exact = [orig for nc, orig in norm_columns if nc == norm_syn]
            if exact:
                result[field_key] = exact[0]
                break
            # Unique substring.
            sub = [orig for nc, orig in norm_columns if norm_syn in nc or nc in norm_syn]
            if len(sub) == 1:
                result[field_key] = sub[0]
                break

    return result


# ── LLM mapping fallback (fires ONLY when the synonym heuristic can't cover the required fields) ──

_REQUIRED_AMOUNT_FIELDS = ("amount", "inflow", "outflow")

_LLM_MAP_SYSTEM = (
    "You map bank-statement spreadsheet COLUMN HEADERS to a fixed set of transaction fields. "
    "Reply with ONLY a compact JSON object {field: column_header}. Use the header strings EXACTLY "
    "as given — never invent a header. Omit a field when no column fits. Fields: date "
    "(transaction date), description (memo/narrative/payee), amount (a single signed amount "
    "column), inflow (money-in column), outflow (money-out column), account (per-row account "
    "name), category, status (a transaction success/failure/pending state column, if one "
    "exists). Map EITHER amount OR inflow+outflow, never both. Ignore running-balance "
    "columns."
)


def _required_ok(mapping: dict) -> bool:
    """True iff ``mapping`` covers what ``validate_mapping`` requires: date + description +
    (a single ``amount`` OR an ``inflow``/``outflow`` column)."""
    return (
        bool(mapping.get("date"))
        and bool(mapping.get("description"))
        and any(mapping.get(k) for k in _REQUIRED_AMOUNT_FIELDS)
    )


async def llm_suggest_mapping(columns: list[str]) -> dict:
    """Ask the LLM (via ``core.llm_seam`` — services must not import ai) to map opaque headers to
    the transaction fields when the deterministic synonym heuristic can't. The reply is UNTRUSTED:
    the caller validates every column against the file before use."""
    from backend.core import llm_seam

    cols = [c for c in columns if c and c.strip()]
    if not cols:
        return {}
    prompt = "Column headers:\n" + "\n".join(f"- {c}" for c in cols)
    reply = await llm_seam.complete(
        prompt,
        system=_LLM_MAP_SYSTEM,
        max_tokens=512,
        source="csv_column_mapping",
        effort="none",  # header→field mapping — mechanical, no reasoning
    )
    parsed = llm_seam.parse_json(reply)
    return parsed if isinstance(parsed, dict) else {}


def _clean_llm_mapping(raw: dict, columns: list[str]) -> dict:
    """Keep only known fields whose value resolves to a REAL column — drops any header the model
    invented (hallucination guard)."""
    out: dict = {}
    for field_key in _MAPPABLE_FIELDS:
        val = raw.get(field_key)
        if not isinstance(val, str) or not val.strip():
            continue
        resolved = _find_column(val, columns)
        if resolved is not None:
            out[field_key] = resolved
    return out


async def suggest_mapping(columns: list[str], *, allow_llm: bool = True) -> dict:
    """Best-effort column→field mapping to PRE-FILL the import form (never auto-applied — the user
    confirms it).

    The deterministic synonym heuristic runs first (free + instant). The LLM fallback fires ONLY
    when the heuristic can't cover the required fields (date + description + amount|inflow/outflow)
    and ``allow_llm`` is set — so a normal bank CSV never reaches the model. The reply is validated
    against the real columns and the heuristic WINS on any field it already resolved. Returns
    ``{"mapping": {...}, "source": "heuristic"|"ai"}``; any LLM failure fails OPEN to the partial
    heuristic."""
    heuristic = auto_detect_mapping(columns)
    if not allow_llm or _required_ok(heuristic):
        return {"mapping": heuristic, "source": "heuristic"}
    try:
        llm = _clean_llm_mapping(await llm_suggest_mapping(columns), columns)
    except Exception:
        logger.warning("csv column-mapping LLM fallback failed; using heuristic", exc_info=True)
        return {"mapping": heuristic, "source": "heuristic"}
    merged = {**llm, **heuristic}  # heuristic wins on any field it resolved
    return {"mapping": merged, "source": "ai" if merged != heuristic else "heuristic"}


def _resolve_account_id(account_id: str | None, account_name_col: str | None, row: dict) -> str:
    """Resolve the destination ONW account id. A chosen ``account_id`` wins; otherwise
    the per-row name from ``account_name_col`` is matched case-insensitively. Raises
    CsvImportError if neither yields a known account."""
    if account_id:
        return account_id
    name = _clean(row.get(account_name_col)) if account_name_col else ""
    if not name:
        raise CsvImportError("No account chosen and the row has no account name.")
    want = name.lower()
    for acct in ledger_service.accounts.all():
        if (acct.get("name") or "").strip().lower() == want:
            return acct["id"]
    raise CsvImportError(f"No account named '{name}'.")


# ── row → draft ──────────────────────────────────────────────────────────────────


def _row_to_draft(
    row: dict,
    mapping: dict,
    account_id: str | None,
    category_rows: list[dict],
    prefix: str = "csv",
) -> dict:
    """Map one CSV row → a two-leg ``create_transaction`` draft. Raises CsvImportError
    with a per-row reason for any unmappable row (caller records it as a skip).

    ``prefix`` namespaces the draft's ``source`` + ``external_source`` so a sheet-backed source
    (XLSX) can reuse this exact mapping logic with its own namespace (``xlsx`` vs ``csv``);
    the CSV path keeps its identical default."""
    date_raw = _clean(row.get(mapping["date"]))
    if not date_raw:
        raise CsvImportError("missing date")
    # Normalize the date: extract the first date token and convert dd/mm/yyyy → ISO.
    date_raw = _extract_date(date_raw)
    if not date_raw:
        raise CsvImportError("missing date")
    description = _clean(row.get(mapping["description"])) or "Imported transaction"

    if "amount" in mapping:
        amount = _parse_amount(row.get(mapping["amount"]))
        if amount is None:
            raise CsvImportError("unparseable amount")
    else:
        inflow = _parse_amount(row.get(mapping["inflow"])) if "inflow" in mapping else None
        outflow = _parse_amount(row.get(mapping["outflow"])) if "outflow" in mapping else None
        # Magnitudes; a sign on either column is respected via abs()/-abs().
        amount = (abs(inflow) if inflow else 0.0) - (abs(outflow) if outflow else 0.0)
    if amount == 0:
        raise CsvImportError("zero amount")

    resolved_account = _resolve_account_id(account_id, mapping.get("account"), row)
    acct = ledger_service.accounts.find_by_id(resolved_account)
    if acct is None:
        raise CsvImportError("account not found")
    currency = (acct.get("currency") or ledger_service.base_currency()).upper()
    magnitude = abs(amount)

    category = ""
    if "category" in mapping:
        # The source column carries a human LABEL (or, less often, an id from a prior export) —
        # resolve it against the live category store; an unrecognized value is dropped (never
        # invents/derives a slug — category-opaque-ids migration).
        raw_cat = _clean(row.get(mapping["category"]))
        if raw_cat:
            resolved = resolve_category_ref(raw_cat, rows=category_rows)
            if resolved:
                category = resolved

    draft: dict = {
        "date": date_raw,
        "description": description,
        "source": f"{prefix}_import",
        "external_refs": [
            {
                "source": prefix,
                "ref": _external_ref(date_raw, description, amount, resolved_account),
            }
        ],
    }
    if amount > 0:
        draft |= {
            "kind": "income",
            "to_account_id": resolved_account,
            "to_amount": magnitude,
            "to_currency": currency,
        }
    else:
        draft |= {
            "kind": "expense",
            "from_account_id": resolved_account,
            "from_amount": magnitude,
            "from_currency": currency,
        }
    if category:
        draft["category"] = category
    return draft


def _external_ref(date_raw: str, description: str, amount: float, account_id: str) -> str:
    """Stable, content-addressed ref so a re-import dedups exactly. Built from the mapped
    fields that identify the row (date · description · signed amount · account); the source
    namespace (``csv`` for CSV, ``xlsx`` for a sheet-backed import) lives in ``external_source``."""
    payload = f"{date_raw}|{description}|{amount:.4f}|{account_id}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


# ── core map step (shared by preview + commit) ──────────────────────────────────


def _map_rows(
    rows: list[dict],
    mapping: dict,
    account_id: str | None,
    prefix: str = "csv",
) -> list[RowResult]:
    """Map every row to a RowResult (draft | skip), flagging duplicates against the
    ledger AND within this file. PURE — reads the ledger/accounts/categories but writes
    NOTHING. ``prefix`` namespaces the drafts' source/external_source (``csv`` default)."""
    category_rows = all_category_rows()  # one store read for the whole file

    results: list[RowResult] = []
    for i, row in enumerate(rows, start=1):
        if not any(_clean(v) for v in row.values()):
            continue  # silently drop fully-blank rows (trailing newlines etc.)
        try:
            draft = _row_to_draft(row, mapping, account_id, category_rows, prefix)
        except CsvImportError as exc:
            results.append(RowResult(line=i, skip_reason=str(exc)))
            continue
        results.append(RowResult(line=i, draft=draft))
    # Distinguish genuinely-identical rows (two same-day $5 coffees) BEFORE dedup so the second
    # is not silently dropped — the content-hash id collision bug (RFC transaction-dedup-engine).
    disambiguate_identical_rows(results)
    flag_duplicates(results)
    return results


def map_sheet_rows(
    columns: list[str],
    rows: list[dict],
    mapping: dict,
    account_id: str | None,
    prefix: str,
) -> list[RowResult]:
    """Validate the mapping + map already-parsed (columns, rows) into RowResults under the
    given external_source ``prefix``. The shared core for a sheet-backed source (XLSX) that has
    decoded its workbook into the SAME (columns, rows) shape ``parse_csv`` produces — so the
    column→field mapping, draft shape, dedup and base math are byte-for-byte the CSV path,
    no duplication. PURE — writes nothing."""
    mapping = validate_mapping(columns, mapping, rows)
    return _map_rows(rows, mapping, account_id, prefix)


# ── public API: preview + commit ─────────────────────────────────────────────────


def preview_import(content: bytes, mapping: dict, account_id: str | None = None) -> PreviewResult:
    """Dry-run: parse + map + report what WOULD import. Writes nothing.

    Returns a PreviewResult with bounded ``sample`` drafts (display shape) and the full
    counts (importable / duplicates / skipped). The caller renders this so the user can
    confirm the mapping before committing."""
    columns, rows = parse_csv(content)
    mapping = validate_mapping(columns, mapping, rows)
    results = _map_rows(rows, mapping, account_id)
    return build_preview(columns, len(rows), results)


def commit_import(content: bytes, mapping: dict, account_id: str | None = None) -> dict:
    """Parse + map + persist the non-duplicate drafts through the ONE ledger choke point
    (``bulk_create_transactions`` → spend-rules + categorization + base math apply).

    Idempotent: a re-import of the same CSV skips everything (zero new rows). Returns
    ``{created, duplicates, skipped, total_rows}``."""
    columns, rows = parse_csv(content)
    mapping = validate_mapping(columns, mapping, rows)
    results = _map_rows(rows, mapping, account_id)
    return build_commit(len(rows), results)
