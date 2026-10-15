"""CAMT.053 transaction import (ISO 20022 XML — the modern European bank statement format).

Like OFX/QIF/MT940, CAMT.053 is STRUCTURED — no column mapping. A CAMT.053 document is
``<Document><BkToCstmrStmt><Stmt>…<Ntry>…</Ntry></Stmt></BkToCstmrStmt></Document>``; each
``<Ntry>`` (booked entry) carries an ``<Amt Ccy="…">`` + a ``<CdtDbtInd>`` (CRDT/DBIT sign),
booking/value dates, and optional ``<NtryDtls><TxDtls>`` with references (AcctSvcrRef /
EndToEndId) and remittance info (``<RmtInf><Ustrd>`` the human description). We build the
SAME two-leg draft shape the other importers commit, then reuse ``import_common``'s
dedup/preview/commit (the ONE ledger choke point ``ledger.bulk_create_transactions`` — so
spend-rules + categorization + base math apply, no per-format duplication).

Parsed with stdlib ``xml.etree.ElementTree`` (no XML dependency). The camt namespace varies
by version (camt.053.001.02 / .001.08 / …), so we match on the element LOCAL name, never a
pinned namespace URI. Idempotency prefers the entry's own reference (``external_source="camt"``
+ ``external_ref="<acct>:<AcctSvcrRef|EndToEndId>"``); when an entry has none, a stable content
hash of the line's identifying fields is used. Money/base figures are NEVER fabricated — the ledger
recomputes the base from the native leg.
"""

from __future__ import annotations

import hashlib
import re
from xml.etree import ElementTree as ET

from backend.core.safe_xml import has_dtd
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


class CamtImportError(ImportError_):
    """A user-correctable problem with the CAMT.053 upload."""


# The external_source namespace this adapter stamps on every draft (== the old ``camt:`` prefix).
# Collapsed onto the ONE statement namespace (RFC 2026-08-01 D2). These refs can never
# string-equal an `importer.external_ref()` content hash, so the collapse unlocks no L0
# hit for this format — it is here so no residual per-format namespace survives to
# re-create the axis conflation. `ofx` deliberately keeps its own: a FITID is native.
EXTERNAL_SOURCE = "statement"


def _looks_like_camt(content: bytes) -> bool:
    """Cheap content sniff so the API can auto-detect format. A CAMT.053 document carries the
    ``BkToCstmrStmt`` root element (and usually a ``camt.053`` namespace marker) — we DON'T
    grab arbitrary XML, only a bank-to-customer statement."""
    head = content[:4096].decode("utf-8", errors="replace")
    return "BkToCstmrStmt" in head or "camt.053" in head.lower()


# ── XML helpers (namespace-agnostic — match on local name) ──────────────────────────


def _local(tag: str) -> str:
    """Strip the ``{namespace}`` prefix ElementTree prepends, leaving the local name."""
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _find(elem: ET.Element, name: str) -> ET.Element | None:
    """First direct-or-descendant child whose LOCAL name is ``name`` (namespace-agnostic)."""
    for child in elem.iter():
        if child is not elem and _local(child.tag) == name:
            return child
    return None


def _find_child(elem: ET.Element, name: str) -> ET.Element | None:
    """First DIRECT child whose local name is ``name`` (no deep descent — used where the
    nesting level matters, e.g. an ``<Ntry>`` directly under ``<Stmt>``)."""
    for child in list(elem):
        if _local(child.tag) == name:
            return child
    return None


def _text(elem: ET.Element | None, name: str) -> str:
    """The text of the first descendant element named ``name`` (``""`` if absent)."""
    found = _find(elem, name) if elem is not None else None
    return (found.text or "").strip() if found is not None else ""


# ── amount / date helpers ────────────────────────────────────────────────────────────


def _parse_amount(raw: str) -> float | None:
    """Parse a CAMT ``<Amt>`` value → float. ISO 20022 uses ``.`` decimals, but accept a
    European ``,`` decimal defensively. Returns None if unparseable."""
    s = (raw or "").strip()
    if not s:
        return None
    s = normalise_decimal(s)
    try:
        return float(s)
    except ValueError:
        return None


def _parse_date(raw: str) -> str:
    """Normalise a CAMT date (``<Dt>YYYY-MM-DD</Dt>`` or ``<DtTm>YYYY-MM-DDThh:mm:ss</DtTm>``)
    to ISO ``YYYY-MM-DD``. Falls back to the raw string if it's not the expected shape."""
    s = (raw or "").strip()
    m = re.match(r"(\d{4}-\d{2}-\d{2})", s)
    return m.group(1) if m else s


def _entry_date(ntry: ET.Element) -> str:
    """The entry's booking date (``<BookgDt>``), falling back to the value date (``<ValDt>``).
    Each is a ``<Dt>`` or ``<DtTm>`` wrapper."""
    for wrapper in ("BookgDt", "ValDt"):
        w = _find(ntry, wrapper)
        if w is not None:
            raw = _text(w, "Dt") or _text(w, "DtTm")
            if raw:
                return _parse_date(raw)
    return ""


# ── parsing ────────────────────────────────────────────────────────────────────────


def parse_camt(content: bytes) -> tuple[str, list[dict]]:
    """Decode + parse CAMT.053 bytes into (account_id_in_file, raw_transactions). Each raw txn
    is ``{date, description, amount, currency, dc, ref}``. Raises CamtImportError on an empty /
    non-CAMT / malformed-XML file. Pure — no ledger access."""
    if not content.strip():
        raise CamtImportError("The file is empty.")
    if not _looks_like_camt(content):
        raise CamtImportError("This does not look like a CAMT.053 file (no BkToCstmrStmt).")
    # A CAMT file is UPLOADED — it arrives from wherever the user got it, and stdlib
    # ElementTree is not hardened against malicious XML. CAMT.053 never needs a DTD, and both
    # billion-laughs and XXE require one, so refusing a document that declares one closes the
    # class. Same rule feeds.py/ibkr.py already apply; this parser had none until 2026-08-28.
    if has_dtd(content):
        raise CamtImportError(
            "This XML declares a DTD or entity, which a CAMT.053 statement never needs — "
            "refusing to parse it."
        )
    try:
        root = ET.fromstring(content)
    except ET.ParseError as exc:
        raise CamtImportError(f"Could not parse the XML: {exc}")

    stmt = _find(root, "Stmt")
    if stmt is None:
        raise CamtImportError("No <Stmt> found in the CAMT.053 document.")

    # The statement account id (<Acct><Id><IBAN> or <Acct><Id><Othr><Id>), used to namespace
    # the ref. Prefer IBAN; else the <Othr><Id> value (the outer <Id> wrapper is just a
    # container whose own text is whitespace — read the <Othr>'s leaf Id).
    acct_elem = _find_child(stmt, "Acct")
    acct = ""
    if acct_elem is not None:
        acct = _text(acct_elem, "IBAN")
        if not acct:
            othr = _find(acct_elem, "Othr")
            acct = _text(othr, "Id") if othr is not None else ""

    raw: list[dict] = []
    for ntry in stmt.iter():
        if _local(ntry.tag) != "Ntry":
            continue
        amt_elem = _find(ntry, "Amt")
        currency = (amt_elem.get("Ccy") or "").strip() if amt_elem is not None else ""
        amount = (amt_elem.text or "").strip() if amt_elem is not None else ""
        dc = _text(ntry, "CdtDbtInd")
        # Reference: prefer the bank's own AcctSvcrRef, then the end-to-end id.
        ref = _text(ntry, "AcctSvcrRef") or _text(ntry, "EndToEndId")
        # Description: remittance info, else additional entry info.
        description = _text(ntry, "Ustrd") or _text(ntry, "AddtlNtryInf")
        raw.append(
            {
                "date": _entry_date(ntry),
                "description": description,
                "amount": amount,
                "currency": currency,
                "dc": dc,
                "ref": ref,
            }
        )
    return acct, raw


# ── raw txn → draft ────────────────────────────────────────────────────────────────


def _txn_to_draft(txn: dict, acct_id_in_file: str, resolved_account: str) -> dict:
    """Map one parsed CAMT entry → a two-leg ``create_transaction`` draft. Raises
    CamtImportError with a per-row reason for any unmappable entry (recorded as a skip)."""
    date_raw = (txn.get("date") or "").strip()
    if not date_raw:
        raise CamtImportError("missing date")
    amount = _parse_amount(txn.get("amount", ""))
    if amount is None:
        raise CamtImportError("unparseable amount")
    if amount == 0:
        raise CamtImportError("zero amount")
    dc = (txn.get("dc") or "").upper()
    if dc not in ("CRDT", "DBIT"):
        raise CamtImportError("missing or invalid CdtDbtInd")

    inflow = dc == "CRDT"
    description = (txn.get("description") or "").strip() or "Imported transaction"
    acct = ledger_service.accounts.find_by_id(resolved_account)
    if acct is None:
        raise CamtImportError("account not found")
    # Native currency: the account's own currency wins; fall back to the entry's <Amt Ccy>,
    # then the base currency (the leg is always in the account's native currency).
    currency = (
        acct.get("currency") or (txn.get("currency") or "") or ledger_service.base_currency()
    ).upper()
    magnitude = abs(amount)

    draft: dict = {
        "date": date_raw,
        "description": description,
        "source": "camt_import",
        "external_refs": [
            {"source": EXTERNAL_SOURCE, "ref": _external_ref(acct_id_in_file, txn, amount)}
        ],
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


def _external_ref(acct_id_in_file: str, txn: dict, amount: float) -> str:
    """The entry's own reference if it has one (``<acct>:<AcctSvcrRef|EndToEndId>``),
    else a stable content hash of the line (CAMT entries don't always carry a ref). Namespaced
    by the statement account so a re-import dedups exactly; the ``camt`` source namespace lives
    in ``external_source``."""
    acct = (acct_id_in_file or "-").strip() or "-"
    ref = (txn.get("ref") or "").strip()
    if ref:
        return f"{acct}:{ref}"
    ident = "|".join(
        [
            (txn.get("date") or "").strip(),
            (txn.get("description") or "").strip(),
            f"{amount:.4f}",
            (txn.get("dc") or "").strip(),
        ]
    )
    digest = hashlib.sha256(ident.encode("utf-8")).hexdigest()[:24]
    return f"{acct}:{digest}"


def _map_rows(raw: list[dict], acct_id_in_file: str, account_id: str | None) -> list[RowResult]:
    """Map every parsed CAMT entry to a RowResult (draft | skip), flagging duplicates. PURE —
    reads the ledger/accounts but writes NOTHING."""
    try:
        resolved = resolve_account(account_id, None)
    except ImportError_ as exc:
        raise CamtImportError(str(exc))
    results: list[RowResult] = []
    for i, txn in enumerate(raw, start=1):
        try:
            draft = _txn_to_draft(txn, acct_id_in_file, resolved)
        except CamtImportError as exc:
            results.append(RowResult(line=i, skip_reason=str(exc)))
            continue
        results.append(RowResult(line=i, draft=draft))
    disambiguate_identical_rows(results)
    flag_duplicates(results)
    return results


# ── public API: preview + commit (mirrors import_ofx) ──────────────────────────────


def preview_import(content: bytes, account_id: str | None = None) -> PreviewResult:
    """Dry-run: parse + map + report what WOULD import. Writes nothing. CAMT is structured,
    so there is no column mapping — only the destination ``account_id``."""
    acct_in_file, raw = parse_camt(content)
    results = _map_rows(raw, acct_in_file, account_id)
    return build_preview([], len(raw), results)


def commit_import(content: bytes, account_id: str | None = None) -> dict:
    """Parse + map + persist the non-duplicate drafts through ``bulk_create_transactions``
    (rules + categorization + base math apply). Idempotent via the ref/content external_ref.
    Returns ``{created, duplicates, skipped, total_rows}``."""
    acct_in_file, raw = parse_camt(content)
    results = _map_rows(raw, acct_in_file, account_id)
    return build_commit(len(raw), results)
