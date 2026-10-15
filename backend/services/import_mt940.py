"""MT940 transaction import (SWIFT bank statement format — European/business banking).

Like OFX/QIF, MT940 is STRUCTURED — no column mapping. An MT940 file is a sequence of
colon-delimited SWIFT tags:

  :20:  statement reference            :60F:/:60M:  opening balance (carries the currency)
  :25:  account identification         :61:         a statement line (value date · D/C · amount · type)
  :28C: statement/sequence number      :86:         info/description for the preceding :61:
                                       :62F:/:62M:  closing balance

Each ``:61:`` line + its following ``:86:`` description → one two-leg ``create_transaction``
draft (sign from the D/C mark, native currency from ``:60F:``/the account), then we reuse
``import_common``'s dedup/preview/commit (the ONE ledger choke point
``ledger.bulk_create_transactions`` — so spend-rules + categorization + base math apply,
no per-format duplication).

MT940 has no globally-unique per-line id, so idempotency uses a stable content hash of the
line's identifying fields: ``external_source="mt940"`` + ``external_ref="<acct>:<sha-of-line>"``. A re-import of the
same file dedups exactly. The ``:61:`` D/C mark drives the sign (C/RC = inflow/income,
D/RD = outflow/expense). Money/base figures are NEVER fabricated — the ledger recomputes the
base from the native leg. No heavy dependency — the tag scanner is hand-rolled (like the OFX
scanner).
"""

from __future__ import annotations

import hashlib
import re

from backend.services import ledger as ledger_service
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


class Mt940ImportError(ImportError_):
    """A user-correctable problem with the MT940 upload."""


# The external_source namespace this adapter stamps on every draft (== the old ``mt940:`` prefix).
# Collapsed onto the ONE statement namespace (RFC 2026-08-01 D2). These refs can never
# string-equal an `importer.external_ref()` content hash, so the collapse unlocks no L0
# hit for this format — it is here so no residual per-format namespace survives to
# re-create the axis conflation. `ofx` deliberately keeps its own: a FITID is native.
EXTERNAL_SOURCE = "statement"


# A ``:61:`` statement line body, e.g. ``2601020102DR4,50NTRFNONREF//bankref``:
#   value date YYMMDD · optional entry date MMDD · D/C mark · optional funds-code letter ·
#   amount (comma decimal) · the rest (transaction type 4-char + refs).
_TAG61_RE = re.compile(
    r"^(?P<valdate>\d{6})"  # value date YYMMDD
    r"(?P<entrydate>\d{4})?"  # optional entry date MMDD
    r"(?P<dc>RC|RD|C|D)"  # debit/credit mark (R = reversal)
    r"(?P<fundscode>[A-Z])?"  # optional funds-code letter
    r"(?P<amount>[\d.,]+)"  # amount (European comma decimal)
    r"(?P<rest>.*)$"  # transaction type + references
)

# An opening-balance tag (:60F: / :60M:), e.g. ``C260101EUR1234,56`` — we want its currency.
_BAL_RE = re.compile(r"^(?P<dc>C|D)(?P<date>\d{6})(?P<ccy>[A-Z]{3})(?P<amount>[\d.,]+)$")


def _looks_like_mt940(content: bytes) -> bool:
    """Cheap content sniff so the API can auto-detect format. An MT940 file is built from
    the SWIFT tags ``:20:`` (reference) + ``:25:`` (account) + ``:61:`` (a statement line)."""
    head = content[:4096].decode("latin-1", errors="replace")
    return bool(re.search(r"^:20:", head, re.MULTILINE)) and (
        ":61:" in head or bool(re.search(r"^:25:", head, re.MULTILINE))
    )


# ── tag scanning ────────────────────────────────────────────────────────────────────


def _scan_tags(text: str) -> list[tuple[str, str]]:
    """Scan the colon-delimited SWIFT tag structure into ``[(tag, value), …]``. A tag starts
    a new ``:NN[A]:`` line; continuation lines (no leading tag) append to the current value
    (``:86:`` info wraps across several lines)."""
    tags: list[tuple[str, str]] = []
    cur_tag: str | None = None
    cur_val: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.rstrip("\r")
        m = re.match(r"^:(\d{2}[A-Z]?):(.*)$", line)
        if m:
            if cur_tag is not None:
                tags.append((cur_tag, "\n".join(cur_val)))
            cur_tag = m.group(1)
            cur_val = [m.group(2)]
        elif cur_tag is not None and line not in ("-", ""):
            cur_val.append(line)  # continuation of the current tag's value
    if cur_tag is not None:
        tags.append((cur_tag, "\n".join(cur_val)))
    return tags


# ── amount / date helpers ────────────────────────────────────────────────────────────


def _parse_amount(raw: str) -> float | None:
    """Parse an MT940 amount (comma decimal, e.g. ``4,50`` or ``1.234,56``) → float.
    Returns None if unparseable."""
    s = (raw or "").strip()
    if not s:
        return None
    s = normalise_decimal(s)
    try:
        return float(s)
    except ValueError:
        return None


def _parse_date(yymmdd: str) -> str:
    """Normalise a 6-digit MT940 value date ``YYMMDD`` to ISO ``YYYY-MM-DD``. The century
    pivot mirrors the QIF importer (YY < 70 → 20xx, else 19xx)."""
    s = (yymmdd or "").strip()
    if len(s) != 6 or not s.isdigit():
        return s
    yy, mm, dd = s[:2], s[2:4], s[4:6]
    century = "20" if int(yy) < 70 else "19"
    return f"{century}{yy}-{mm}-{dd}"


def _clean_86(value: str) -> str:
    """Flatten a ``:86:`` info block into a single description line. Structured ``:86:`` uses
    ``>NN`` subfield separators (German banks); we strip those markers and join the parts."""
    text = (value or "").replace("\n", " ").strip()
    if text.startswith(">") or re.search(r">\d{2}", text):
        parts = [p.strip() for p in re.split(r">\d{2}", text) if p.strip()]
        text = " ".join(parts)
    return re.sub(r"\s+", " ", text).strip()


# ── parsing ────────────────────────────────────────────────────────────────────────


def parse_mt940(content: bytes) -> tuple[str, str, list[dict]]:
    """Decode + scan MT940 bytes into ``(account_id_in_file, currency, raw_transactions)``.
    Each raw txn is ``{date, description, amount, dc, line_ref}``. Raises Mt940ImportError on
    an empty / unrecognised file. Pure — no ledger access."""
    text = content.decode("utf-8-sig", errors="replace")
    if not text.strip():
        raise Mt940ImportError("The file is empty.")
    if not _looks_like_mt940(content):
        raise Mt940ImportError("This does not look like an MT940 file (no :20:/:61: tags).")

    tags = _scan_tags(text)
    acct = ""
    currency = ""
    raw: list[dict] = []
    pending: dict | None = None

    for tag, value in tags:
        base_tag = tag[:2]
        if tag == "25":
            acct = value.strip().split("\n", 1)[0].strip()
        elif tag in ("60F", "60M") and not currency:
            m = _BAL_RE.match(value.strip().replace("\n", ""))
            if m:
                currency = m.group("ccy")
        elif base_tag == "61":
            if pending is not None:
                raw.append(pending)  # a :61: with no following :86:
            m = _TAG61_RE.match(value.strip().split("\n", 1)[0].strip())
            if not m:
                pending = None
                continue
            pending = {
                "date": _parse_date(m.group("valdate")),
                "amount": m.group("amount"),
                "dc": m.group("dc"),
                "description": "",
                "line_ref": value.strip(),
            }
        elif base_tag == "86" and pending is not None:
            pending["description"] = _clean_86(value)
            raw.append(pending)
            pending = None

    if pending is not None:
        raw.append(pending)

    if not raw:
        raise Mt940ImportError("No :61: statement lines found in the MT940 file.")
    return acct, currency, raw


# ── raw txn → draft ────────────────────────────────────────────────────────────────


def _txn_to_draft(
    txn: dict, acct_id_in_file: str, file_currency: str, resolved_account: str
) -> dict:
    """Map one scanned MT940 line → a two-leg ``create_transaction`` draft. Raises
    Mt940ImportError with a per-row reason for any unmappable line (recorded as a skip)."""
    date_raw = (txn.get("date") or "").strip()
    if not date_raw:
        raise Mt940ImportError("missing date")
    amount = _parse_amount(txn.get("amount", ""))
    if amount is None:
        raise Mt940ImportError("unparseable amount")
    if amount == 0:
        raise Mt940ImportError("zero amount")

    dc = (txn.get("dc") or "").upper()
    # C/RC = credit (money in / income); D/RD = debit (money out / expense).
    inflow = dc in ("C", "RC")

    description = (txn.get("description") or "").strip() or "Imported transaction"
    acct = ledger_service.accounts.find_by_id(resolved_account)
    if acct is None:
        raise Mt940ImportError("account not found")
    # Native currency: the account's own currency wins; fall back to the file's :60F: ccy,
    # then the base currency (the leg is always in the account's native currency).
    currency = (acct.get("currency") or file_currency or ledger_service.base_currency()).upper()
    magnitude = abs(amount)

    draft: dict = {
        "date": date_raw,
        "description": description,
        "source": "mt940_import",
        "external_refs": [{"source": EXTERNAL_SOURCE, "ref": _external_ref(acct_id_in_file, txn)}],
    }
    if inflow:
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
    return draft


def _external_ref(acct_id_in_file: str, txn: dict) -> str:
    """Stable content-addressed ref so a re-import dedups exactly (MT940 has no native id).
    Built from the full ``:61:`` line + the description (the line's identifying content),
    namespaced by the statement account: ``<acct>:<sha-of-line>``; the ``mt940`` source namespace
    lives in ``external_source``."""
    acct = (acct_id_in_file or "-").strip() or "-"
    ident = "|".join([(txn.get("line_ref") or "").strip(), (txn.get("description") or "").strip()])
    digest = hashlib.sha256(ident.encode("utf-8")).hexdigest()[:24]
    return f"{acct}:{digest}"


def _map_rows(
    raw: list[dict], acct_id_in_file: str, file_currency: str, account_id: str | None
) -> list[RowResult]:
    """Map every scanned MT940 line to a RowResult (draft | skip), flagging duplicates.
    PURE — reads the ledger/accounts but writes NOTHING."""
    try:
        resolved = resolve_account(account_id, None)
    except ImportError_ as exc:
        raise Mt940ImportError(str(exc))
    results: list[RowResult] = []
    for i, txn in enumerate(raw, start=1):
        try:
            draft = _txn_to_draft(txn, acct_id_in_file, file_currency, resolved)
        except Mt940ImportError as exc:
            results.append(RowResult(line=i, skip_reason=str(exc)))
            continue
        results.append(RowResult(line=i, draft=draft))
    disambiguate_identical_rows(results)
    flag_duplicates(results)
    return results


# ── public API: preview + commit (mirrors import_ofx) ──────────────────────────────


def preview_import(content: bytes, account_id: str | None = None) -> PreviewResult:
    """Dry-run: parse + map + report what WOULD import. Writes nothing. MT940 is structured,
    so there is no column mapping — only the destination ``account_id``."""
    acct_in_file, currency, raw = parse_mt940(content)
    results = _map_rows(raw, acct_in_file, currency, account_id)
    return build_preview([], len(raw), results)


def commit_import(content: bytes, account_id: str | None = None) -> dict:
    """Parse + map + persist the non-duplicate drafts through ``bulk_create_transactions``
    (rules + categorization + base math apply). Idempotent via the content-hash external_ref.
    Returns ``{created, duplicates, skipped, total_rows}``."""
    acct_in_file, currency, raw = parse_mt940(content)
    results = _map_rows(raw, acct_in_file, currency, account_id)
    return build_commit(len(raw), results)
