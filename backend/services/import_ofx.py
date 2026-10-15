"""OFX transaction import (banks/brokerages that export Open Financial Exchange).

Unlike CSV, OFX is STRUCTURED — there is no column mapping. We extract each
``<STMTTRN>`` block (DTPOSTED · TRNAMT · NAME/MEMO · FITID) and build the SAME two-leg
draft shape the CSV importer commits, then reuse ``import_common``'s dedup/preview/commit
(the ONE ledger choke point ``ledger.bulk_create_transactions`` — so spend-rules +
categorization + base math apply, no per-format duplication).

Both flavours are handled with one tag-pair scanner (no XML lib): OFX 1.x is SGML with
unclosed value tags (``<TRNAMT>-12.00`` runs to the next ``<``), OFX 2.x is well-formed
XML (``<TRNAMT>-12.00</TRNAMT>``). The scanner reads ``<TAG>value`` up to the next ``<``,
which is correct for both — a present ``</TAG>`` just becomes the terminator.

Idempotency: ``external_source="ofx"`` + ``external_ref="<acct>:<FITID>"`` — the file's own
globally-unique id, so a re-import (even a wider date range that overlaps) dedups exactly. TRNAMT is signed
native (positive = inflow/income, negative = outflow/expense), mirroring the CSV
single-amount convention. Money/base figures are NEVER fabricated — the ledger recomputes
the base from the native leg.
"""

from __future__ import annotations

import re

from backend.services import ledger as ledger_service
from backend.services.import_common import (
    ImportError_,
    PreviewResult,
    RowResult,
    build_commit,
    build_preview,
    flag_duplicates,
    normalise_decimal,
    resolve_account,
)


class OfxImportError(ImportError_):
    """A user-correctable problem with the OFX upload."""


# The external_source namespace this adapter stamps on every draft (== the old ``ofx:`` prefix).
EXTERNAL_SOURCE = "ofx"


# ── low-level OFX field scanning (SGML 1.x + XML 2.x, no XML lib) ───────────────────

_STMTTRN_RE = re.compile(r"<STMTTRN>(.*?)</STMTTRN>", re.IGNORECASE | re.DOTALL)


def _tag_value(block: str, tag: str) -> str:
    """Read the value of ``<TAG>`` inside ``block``, up to the next ``<`` (works for both
    the SGML unclosed form and the XML closed form). Returns ``""`` if the tag is absent."""
    m = re.search(rf"<{tag}>([^<\r\n]*)", block, re.IGNORECASE)
    return m.group(1).strip() if m else ""


def _looks_like_ofx(content: bytes) -> bool:
    """Cheap content sniff so the API can auto-detect format (an OFX file always carries an
    ``<OFX>`` root or an ``OFXHEADER`` line)."""
    head = content[:4096].decode("latin-1", errors="replace").upper()
    return "<OFX>" in head or "OFXHEADER" in head


def _account_id(text: str) -> str:
    """The source account id from the statement header (``<ACCTID>`` under BANKACCTFROM /
    CCACCTFROM), used to namespace the FITID. ``""`` if the file omits it."""
    return _tag_value(text, "ACCTID")


# ── amount / date helpers ──────────────────────────────────────────────────────────


def _parse_amount(raw: str) -> float | None:
    """Parse an OFX TRNAMT → float. OFX uses ``.`` decimals; some European exporters emit a
    ``,`` decimal — accept either. Returns None if unparseable."""
    s = (raw or "").strip()
    if not s:
        return None
    s = normalise_decimal(s)
    try:
        return float(s)
    except ValueError:
        return None


def _parse_date(raw: str) -> str:
    """Normalise an OFX DTPOSTED (``YYYYMMDD`` or ``YYYYMMDDHHMMSS[.xxx][tz]``) to an
    ISO ``YYYY-MM-DD`` date. Falls back to the raw string if it's not the expected shape."""
    s = (raw or "").strip()
    digits = re.match(r"(\d{4})(\d{2})(\d{2})", s)
    if digits:
        return f"{digits.group(1)}-{digits.group(2)}-{digits.group(3)}"
    return s


# ── parsing ────────────────────────────────────────────────────────────────────────


def parse_ofx(content: bytes) -> tuple[str, list[dict]]:
    """Decode + scan OFX bytes into (account_id, raw_transactions). Each raw txn is
    ``{date, description, amount, fitid}``. Raises OfxImportError on an empty/unrecognised
    file. Pure — no ledger access."""
    text = content.decode("utf-8-sig", errors="replace")
    if not text.strip():
        raise OfxImportError("The file is empty.")
    if not _looks_like_ofx(content):
        raise OfxImportError("This does not look like an OFX file (no <OFX> root found).")

    acct = _account_id(text)
    raw: list[dict] = []
    for block in _STMTTRN_RE.findall(text):
        raw.append(
            {
                "date": _parse_date(_tag_value(block, "DTPOSTED")),
                "description": _tag_value(block, "NAME") or _tag_value(block, "MEMO"),
                "amount": _tag_value(block, "TRNAMT"),
                "fitid": _tag_value(block, "FITID"),
            }
        )
    return acct, raw


# ── raw txn → draft ────────────────────────────────────────────────────────────────


def _txn_to_draft(txn: dict, acct_id_in_file: str, resolved_account: str) -> dict:
    """Map one scanned OFX txn → a two-leg ``create_transaction`` draft. Raises
    OfxImportError with a per-row reason for any unmappable txn (recorded as a skip)."""
    date_raw = (txn.get("date") or "").strip()
    if not date_raw:
        raise OfxImportError("missing date")
    amount = _parse_amount(txn.get("amount", ""))
    if amount is None:
        raise OfxImportError("unparseable amount")
    if amount == 0:
        raise OfxImportError("zero amount")
    fitid = (txn.get("fitid") or "").strip()
    if not fitid:
        raise OfxImportError("missing FITID")

    description = (txn.get("description") or "").strip() or "Imported transaction"
    acct = ledger_service.accounts.find_by_id(resolved_account)
    if acct is None:
        raise OfxImportError("account not found")
    currency = (acct.get("currency") or ledger_service.base_currency()).upper()
    magnitude = abs(amount)

    draft: dict = {
        "date": date_raw,
        "description": description,
        "source": "ofx_import",
        "external_refs": [
            {"source": EXTERNAL_SOURCE, "ref": _external_ref(acct_id_in_file, fitid)}
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
    return draft


def _external_ref(acct_id_in_file: str, fitid: str) -> str:
    """The file's own globally-unique ref, namespaced so a re-import dedups exactly.
    ``<acct>:<FITID>`` (acct = the statement's ACCTID, falling back to ``-`` if absent);
    the ``ofx`` source namespace lives in ``external_source``."""
    acct = (acct_id_in_file or "-").strip() or "-"
    return f"{acct}:{fitid}"


def _map_rows(raw: list[dict], acct_id_in_file: str, account_id: str | None) -> list[RowResult]:
    """Map every scanned OFX txn to a RowResult (draft | skip), flagging duplicates. PURE —
    reads the ledger/accounts but writes NOTHING."""
    try:
        resolved = resolve_account(account_id, None)
    except ImportError_ as exc:
        raise OfxImportError(str(exc))
    results: list[RowResult] = []
    for i, txn in enumerate(raw, start=1):
        try:
            draft = _txn_to_draft(txn, acct_id_in_file, resolved)
        except OfxImportError as exc:
            results.append(RowResult(line=i, skip_reason=str(exc)))
            continue
        results.append(RowResult(line=i, draft=draft))
    flag_duplicates(results)
    return results


# ── public API: preview + commit (mirrors import_csv) ──────────────────────────────


def preview_import(content: bytes, account_id: str | None = None) -> PreviewResult:
    """Dry-run: parse + map + report what WOULD import. Writes nothing. OFX is structured,
    so there is no column mapping — only the destination ``account_id``."""
    acct_in_file, raw = parse_ofx(content)
    results = _map_rows(raw, acct_in_file, account_id)
    return build_preview([], len(raw), results)


def commit_import(content: bytes, account_id: str | None = None) -> dict:
    """Parse + map + persist the non-duplicate drafts through ``bulk_create_transactions``
    (rules + categorization + base math apply). Idempotent via the FITID-based external_ref.
    Returns ``{created, duplicates, skipped, total_rows}``."""
    acct_in_file, raw = parse_ofx(content)
    results = _map_rows(raw, acct_in_file, account_id)
    return build_commit(len(raw), results)
