"""QIF transaction import (Quicken Interchange Format — older banks/desktop apps).

Like OFX, QIF is STRUCTURED — no column mapping. A QIF file is a ``!Type:<kind>`` header
followed by records terminated by a ``^`` line; each record is single-letter fields:
``D`` date · ``T`` amount (signed) · ``P`` payee · ``M`` memo · ``L`` category. We build the
SAME two-leg draft shape the CSV importer commits, then reuse ``import_common``'s
dedup/preview/commit (the ONE ledger choke point ``ledger.bulk_create_transactions`` — so
spend-rules + categorization + base math apply, no per-format duplication).

QIF has no native per-row unique id, so idempotency uses a stable content hash of the
record's identifying fields: ``external_source="qif"`` + ``external_ref="<sha-of-row>"`` (date ·
payee/memo · signed amount · account · category). A re-import of the same file dedups exactly.
``T`` is signed native (positive = inflow/income, negative = outflow/expense). Money/base
figures are NEVER fabricated — the ledger recomputes the base from the native leg.
"""

from __future__ import annotations

import hashlib
import re

from backend.services import ledger as ledger_service
from backend.services.categories import all_category_rows, resolve_category_ref
from backend.services.import_common import (
    ImportError_,
    PreviewResult,
    RowResult,
    build_commit,
    build_preview,
    disambiguate_identical_rows,
    flag_duplicates,
    normalise_decimal,
    resolve_account,
)


class QifImportError(ImportError_):
    """A user-correctable problem with the QIF upload."""


# The external_source namespace this adapter stamps on every draft (== the old ``qif:`` prefix).
EXTERNAL_SOURCE = "qif"


# QIF record types we treat as cash-flow statements. Investment (``!Type:Invst``) carries a
# different field set (Q/buy/sell) and is out of scope for the cash ledger — we reject it.
_CASH_TYPES = {"bank", "cash", "ccard", "creditcard", "oth a", "oth l", "otha", "othl"}


def _looks_like_qif(content: bytes) -> bool:
    """Cheap content sniff so the API can auto-detect format (a QIF file opens with a
    ``!Type:`` / ``!Account`` / ``!Option`` directive)."""
    head = content[:512].decode("latin-1", errors="replace").lstrip().upper()
    return head.startswith("!TYPE:") or head.startswith("!ACCOUNT") or head.startswith("!OPTION")


# ── amount / date helpers ──────────────────────────────────────────────────────────


def _parse_amount(raw: str) -> float | None:
    """Parse a QIF ``T``/``U`` amount → float. Strips currency symbols, thousands commas
    and parenthesised negatives; accepts a European ``,`` decimal. Returns None if bad."""
    s = (raw or "").strip()
    if not s:
        return None
    negative = s.startswith("(") and s.endswith(")")
    if negative:
        s = s[1:-1]
    s = re.sub(r"[^0-9,.\-]", "", s)
    s = normalise_decimal(s)
    if s in ("", "-", ".", "-."):
        return None
    try:
        val = float(s)
    except ValueError:
        return None
    return -val if negative else val


def _parse_date(raw: str) -> str:
    """Normalise a QIF ``D`` date to ISO ``YYYY-MM-DD``. QIF dates are notoriously varied —
    ``M/D'YY``, ``MM/DD/YYYY``, ``D/M/YYYY``, ``YYYY-MM-DD``. We handle the common US/ISO
    shapes; anything else is passed through verbatim (the ledger accepts a string date)."""
    s = (raw or "").strip().replace("'", "/").replace(".", "/")
    if re.match(r"^\d{4}-\d{2}-\d{2}$", s):
        return s
    parts = re.split(r"[/]", s)
    if len(parts) == 3:
        a, b, c = (p.strip() for p in parts)
        if len(a) == 4:  # YYYY/MM/DD
            y, mo, d = a, b, c
        else:  # MM/DD/YY(YY) — Quicken's default ordering
            mo, d, y = a, b, c
            if len(y) == 2:
                y = ("20" if int(y) < 70 else "19") + y
        try:
            return f"{int(y):04d}-{int(mo):02d}-{int(d):02d}"
        except ValueError:
            return s
    return s


# ── parsing ────────────────────────────────────────────────────────────────────────


def parse_qif(content: bytes) -> list[dict]:
    """Decode + parse QIF bytes into a list of raw txn records (``{date, payee, memo,
    amount, category}``). Raises QifImportError on an empty file, a missing/unknown
    ``!Type`` header, or an investment-type file (out of scope). Pure — no ledger access."""
    text = content.decode("utf-8-sig", errors="replace")
    if not text.strip():
        raise QifImportError("The file is empty.")

    lines = text.splitlines()
    qif_type: str | None = None
    raw: list[dict] = []
    cur: dict = {}

    for line in lines:
        line = line.rstrip("\r")
        if not line:
            continue
        code, value = line[0], line[1:].strip()
        if code == "!":
            directive = value.lower()
            if directive.startswith("type:"):
                qif_type = directive[len("type:") :].strip()
            continue
        if code == "^":  # end of one record
            if cur:
                raw.append(cur)
            cur = {}
            continue
        if code == "D":
            cur["date"] = _parse_date(value)
        elif code in ("T", "U"):  # T = amount; U = same (duplicate some exporters emit)
            cur.setdefault("amount", value)
        elif code == "P":
            cur["payee"] = value
        elif code == "M":
            cur["memo"] = value
        elif code == "L":
            cur["category"] = value
        # other QIF codes (N, C, A, S/E/$ splits, …) are ignored for the cash ledger
    if cur:  # a trailing record with no closing ^
        raw.append(cur)

    if qif_type is None:
        raise QifImportError("No '!Type:' header found — not a recognised QIF file.")
    if qif_type not in _CASH_TYPES:
        raise QifImportError(
            f"QIF type '!Type:{qif_type}' is not a cash/bank statement and can't be imported."
        )
    return raw


# ── raw txn → draft ────────────────────────────────────────────────────────────────


def _txn_to_draft(txn: dict, resolved_account: str, category_rows: list[dict]) -> dict:
    """Map one QIF record → a two-leg ``create_transaction`` draft. Raises QifImportError
    with a per-row reason for any unmappable record (recorded as a skip)."""
    date_raw = (txn.get("date") or "").strip()
    if not date_raw:
        raise QifImportError("missing date")
    amount = _parse_amount(txn.get("amount", ""))
    if amount is None:
        raise QifImportError("unparseable amount")
    if amount == 0:
        raise QifImportError("zero amount")

    description = (txn.get("payee") or txn.get("memo") or "").strip() or "Imported transaction"
    acct = ledger_service.accounts.find_by_id(resolved_account)
    if acct is None:
        raise QifImportError("account not found")
    currency = (acct.get("currency") or ledger_service.base_currency()).upper()
    magnitude = abs(amount)

    category = ""
    raw_cat = (txn.get("category") or "").strip()
    if raw_cat:
        # QIF categories can be "Parent:Child" or a "[Transfer Account]"; take the leaf (a
        # human LABEL, or occasionally an id) and resolve it against the live category store —
        # never derive/invent a slug (category-opaque-ids migration).
        leaf = raw_cat.strip("[]").split(":")[-1].strip()
        if leaf:
            resolved = resolve_category_ref(leaf, rows=category_rows)
            if resolved:
                category = resolved

    draft: dict = {
        "date": date_raw,
        "description": description,
        "source": "import",
        "external_refs": [
            {"source": EXTERNAL_SOURCE, "ref": _external_ref(txn, resolved_account, amount)}
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


def _external_ref(txn: dict, account_id: str, amount: float) -> str:
    """Stable content-addressed ref so a re-import dedups exactly (QIF has no native id).
    Built from the record's identifying fields (date · payee/memo · signed amount · account
    · category); the ``qif`` source namespace lives in ``external_source``."""
    ident = "|".join(
        [
            (txn.get("date") or "").strip(),
            (txn.get("payee") or txn.get("memo") or "").strip(),
            f"{amount:.4f}",
            account_id,
            (txn.get("category") or "").strip(),
        ]
    )
    return hashlib.sha256(ident.encode("utf-8")).hexdigest()[:24]


def _map_rows(raw: list[dict], account_id: str | None) -> list[RowResult]:
    """Map every QIF record to a RowResult (draft | skip), flagging duplicates. PURE —
    reads the ledger/accounts/categories but writes NOTHING."""
    try:
        resolved = resolve_account(account_id, None)
    except ImportError_ as exc:
        raise QifImportError(str(exc))
    category_rows = all_category_rows()  # one store read for the whole file
    results: list[RowResult] = []
    for i, txn in enumerate(raw, start=1):
        try:
            draft = _txn_to_draft(txn, resolved, category_rows)
        except QifImportError as exc:
            results.append(RowResult(line=i, skip_reason=str(exc)))
            continue
        results.append(RowResult(line=i, draft=draft))
    # Keep genuinely-identical rows distinct before dedup (content-hash collision bug —
    # RFC transaction-dedup-engine §id fix).
    disambiguate_identical_rows(results)
    flag_duplicates(results)
    return results


# ── public API: preview + commit (mirrors import_csv) ──────────────────────────────


def preview_import(content: bytes, account_id: str | None = None) -> PreviewResult:
    """Dry-run: parse + map + report what WOULD import. Writes nothing. QIF is structured,
    so there is no column mapping — only the destination ``account_id``."""
    raw = parse_qif(content)
    results = _map_rows(raw, account_id)
    return build_preview([], len(raw), results)


def commit_import(content: bytes, account_id: str | None = None) -> dict:
    """Parse + map + persist the non-duplicate drafts through ``bulk_create_transactions``
    (rules + categorization + base math apply). Idempotent via the content-hash external_ref.
    Returns ``{created, duplicates, skipped, total_rows}``."""
    raw = parse_qif(content)
    results = _map_rows(raw, account_id)
    return build_commit(len(raw), results)
