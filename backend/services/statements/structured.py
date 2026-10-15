"""Deterministic adapters: the structured formats (OFX/QIF/MT940/CAMT) → the canonical
statement contract, so EVERY import format rides the ONE decision pipeline (bands, weak
twins, ledger check, statement records, rollback) instead of the legacy per-format
preview/commit pair.

Each adapter reuses its format module's existing PURE parser (``parse_ofx`` & friends —
never forked) and maps raw records to ``CanonicalRow``s: zero LLM calls, ``confidence=1.0``,
unreadable records become explicit skip rows (the decision preview offers them with a
reason, byte-identical to how the legacy path reported skips).

Dedup-id continuity: rows carry the format's NATIVE ``{source, ref}`` provenance pair —
minted by the SAME ``_external_ref`` helpers the legacy importers used — so a file booked
through either path hard-dedups on re-import (``ofx`` + ``<acct>:<FITID>``, ``mt940`` +
``<acct>:<sha>``, ``camt`` + ``<acct>:<ref|sha>``). QIF's legacy hash folded in the RESOLVED ledger account and
category — unknowable at parse time — so QIF rows fall to the standard draft-time content
hash instead; the batch match engine's near-duplicate band is the (loud) net for the one
legacy-QIF re-import that crosses the id change.
"""

from __future__ import annotations

import re
from xml.etree import ElementTree as ET

from backend.core.safe_xml import has_dtd
from backend.services import import_camt, import_mt940, import_ofx, import_qif
from backend.services.statements.canonical import CanonicalRow, ParseResult, StatementMeta
from backend.services.statements.guards import apply_guards
from backend.services.statements.tiers import StatementParseError

STRUCTURED_FORMATS = frozenset({"ofx", "qif", "mt940", "camt"})


def _skip_row(reason: str) -> CanonicalRow:
    return CanonicalRow(date="", direction="OUT", amount=0.0, skip=True, skip_reason=reason)


def _iso_or_none(raw: str) -> str | None:
    s = (raw or "").strip()
    return s if re.match(r"^\d{4}-\d{2}-\d{2}$", s) else None


# ── OFX ──────────────────────────────────────────────────────────────────────────────


def _ofx_meta(text: str) -> StatementMeta:
    """Header facts the OFX file declares: CURDEF, the <BANKTRANLIST> DTSTART/DTEND period,
    and the LEDGERBAL closing balance (no opening → the reconcile identity stays unknown;
    completeness then rests on the bank-complete gate, not the balance check)."""
    meta = StatementMeta(sign_convention="bank")
    meta.currency = import_ofx._tag_value(text, "CURDEF").upper() or None
    meta.period_start = _iso_or_none(import_ofx._parse_date(import_ofx._tag_value(text, "DTSTART")))
    meta.period_end = _iso_or_none(import_ofx._parse_date(import_ofx._tag_value(text, "DTEND")))
    m = re.search(r"<LEDGERBAL>(.*?)(</LEDGERBAL>|<AVAILBAL>|$)", text, re.IGNORECASE | re.DOTALL)
    if m:
        meta.closing_balance = import_ofx._parse_amount(import_ofx._tag_value(m.group(1), "BALAMT"))
    acct = import_ofx._account_id(text)
    if acct and acct[-4:].isdigit():
        meta.account_last4s = [acct[-4:]]
    return meta


def _ofx_rows(raw: list[dict], acct_in_file: str) -> list[CanonicalRow]:
    rows: list[CanonicalRow] = []
    for txn in raw:
        date = (txn.get("date") or "").strip()
        if not date:
            rows.append(_skip_row("missing date"))
            continue
        amount = import_ofx._parse_amount(txn.get("amount", ""))
        if amount is None:
            rows.append(_skip_row("unparseable amount"))
            continue
        if amount == 0:
            rows.append(_skip_row("zero amount"))
            continue
        fitid = (txn.get("fitid") or "").strip()
        if not fitid:
            rows.append(_skip_row("missing FITID"))
            continue
        rows.append(
            CanonicalRow(
                date=date,
                direction="IN" if amount > 0 else "OUT",
                amount=abs(amount),
                description=(txn.get("description") or "").strip(),
                external_refs=[
                    {
                        "source": import_ofx.EXTERNAL_SOURCE,
                        "ref": import_ofx._external_ref(acct_in_file, fitid),
                    }
                ],
            )
        )
    return rows


def _ofx_canonical(content: bytes) -> ParseResult:
    acct_in_file, raw = import_ofx.parse_ofx(content)
    text = content.decode("utf-8-sig", errors="replace")
    return ParseResult(meta=_ofx_meta(text), rows=_ofx_rows(raw, acct_in_file), tier="structured")


# ── QIF ──────────────────────────────────────────────────────────────────────────────


def _qif_rows(raw: list[dict]) -> list[CanonicalRow]:
    rows: list[CanonicalRow] = []
    for txn in raw:
        date = (txn.get("date") or "").strip()
        if not date:
            rows.append(_skip_row("missing date"))
            continue
        amount = import_qif._parse_amount(txn.get("amount", ""))
        if amount is None:
            rows.append(_skip_row("unparseable amount"))
            continue
        if amount == 0:
            rows.append(_skip_row("zero amount"))
            continue
        # "Parent:Child" / "[Transfer Account]" → the leaf label, resolved at draft time.
        raw_cat = (txn.get("category") or "").strip()
        leaf = raw_cat.strip("[]").split(":")[-1].strip() if raw_cat else ""
        rows.append(
            CanonicalRow(
                date=date,
                direction="IN" if amount > 0 else "OUT",
                amount=abs(amount),
                description=(txn.get("payee") or txn.get("memo") or "").strip(),
                payee=(txn.get("payee") or "").strip() or None,
                category_ref=leaf or None,
            )
        )
    return rows


def _qif_canonical(content: bytes) -> ParseResult:
    raw = import_qif.parse_qif(content)
    return ParseResult(
        meta=StatementMeta(sign_convention="bank"), rows=_qif_rows(raw), tier="structured"
    )


# ── MT940 ────────────────────────────────────────────────────────────────────────────


def _mt940_balance(value: str) -> tuple[str | None, str | None, float | None]:
    """(iso_date, currency, signed_amount) from a :60F:/:62F: tag body — D = overdrawn."""
    m = import_mt940._BAL_RE.match(value.strip().replace("\n", ""))
    if not m:
        return None, None, None
    amount = import_mt940._parse_amount(m.group("amount"))
    if amount is not None and m.group("dc") == "D":
        amount = -amount
    return import_mt940._parse_date(m.group("date")), m.group("ccy"), amount


def _mt940_meta(text: str) -> StatementMeta:
    """:60F:/:62F: carry the statement's own opening/closing balances AND dates — enough for
    the reconcile identity to prove the parse complete (the completeness gate's first leg)."""
    meta = StatementMeta(sign_convention="bank")
    for tag, value in import_mt940._scan_tags(text):
        if tag in ("60F", "60M") and meta.opening_balance is None:
            date, ccy, amount = _mt940_balance(value)
            meta.period_start = _iso_or_none(date or "")
            meta.currency = meta.currency or ccy
            meta.opening_balance = amount
        elif tag in ("62F", "62M"):
            date, ccy, amount = _mt940_balance(value)
            meta.period_end = _iso_or_none(date or "") or meta.period_end
            meta.currency = meta.currency or ccy
            meta.closing_balance = amount
    return meta


def _mt940_rows(raw: list[dict], acct_in_file: str) -> list[CanonicalRow]:
    rows: list[CanonicalRow] = []
    for txn in raw:
        date = (txn.get("date") or "").strip()
        if not date:
            rows.append(_skip_row("missing date"))
            continue
        amount = import_mt940._parse_amount(txn.get("amount", ""))
        if amount is None:
            rows.append(_skip_row("unparseable amount"))
            continue
        if amount == 0:
            rows.append(_skip_row("zero amount"))
            continue
        rows.append(
            CanonicalRow(
                date=date,
                direction="IN" if (txn.get("dc") or "").upper() in ("C", "RC") else "OUT",
                amount=abs(amount),
                description=(txn.get("description") or "").strip(),
                external_refs=[
                    {
                        "source": import_mt940.EXTERNAL_SOURCE,
                        "ref": import_mt940._external_ref(acct_in_file, txn),
                    }
                ],
            )
        )
    return rows


def _mt940_canonical(content: bytes) -> ParseResult:
    acct_in_file, _ccy, raw = import_mt940.parse_mt940(content)
    text = content.decode("utf-8-sig", errors="replace")
    return ParseResult(
        meta=_mt940_meta(text), rows=_mt940_rows(raw, acct_in_file), tier="structured"
    )


# ── CAMT.053 ─────────────────────────────────────────────────────────────────────────


def _camt_meta(content: bytes) -> StatementMeta:
    """OPBD/CLBD balances + the FrToDt period, namespace-agnostic (reuses import_camt's
    local-name helpers). All optional — an entry-only document still parses."""
    meta = StatementMeta(sign_convention="bank")
    # Same uploaded bytes as parse_camt, so the same DTD refusal applies. Returns empty meta
    # rather than raising: this is the OPTIONAL metadata read, and its contract is already
    # "all optional — an entry-only document still parses".
    if has_dtd(content):
        return meta
    try:
        root = ET.fromstring(content)
    except ET.ParseError:
        return meta
    stmt = import_camt._find(root, "Stmt")
    if stmt is None:
        return meta
    period = import_camt._find(stmt, "FrToDt")
    if period is not None:
        meta.period_start = _iso_or_none(
            import_camt._parse_date(import_camt._text(period, "FrDtTm"))
        )
        meta.period_end = _iso_or_none(import_camt._parse_date(import_camt._text(period, "ToDtTm")))
    for bal in stmt.iter():
        if import_camt._local(bal.tag) != "Bal":
            continue
        code = import_camt._text(bal, "Cd").upper()
        if code not in ("OPBD", "CLBD"):
            continue
        amt_elem = import_camt._find(bal, "Amt")
        amount = import_camt._parse_amount((amt_elem.text or "") if amt_elem is not None else "")
        if amount is None:
            continue
        if import_camt._text(bal, "CdtDbtInd").upper() == "DBIT":
            amount = -amount
        if code == "OPBD" and meta.opening_balance is None:
            meta.opening_balance = amount
            meta.currency = meta.currency or (amt_elem.get("Ccy") or "").upper() or None
        elif code == "CLBD":
            meta.closing_balance = amount
            meta.currency = meta.currency or (amt_elem.get("Ccy") or "").upper() or None
    return meta


def _camt_rows(raw: list[dict], acct_in_file: str) -> list[CanonicalRow]:
    rows: list[CanonicalRow] = []
    for txn in raw:
        date = (txn.get("date") or "").strip()
        if not date:
            rows.append(_skip_row("missing date"))
            continue
        amount = import_camt._parse_amount(txn.get("amount", ""))
        if amount is None:
            rows.append(_skip_row("unparseable amount"))
            continue
        if amount == 0:
            rows.append(_skip_row("zero amount"))
            continue
        dc = (txn.get("dc") or "").upper()
        if dc not in ("CRDT", "DBIT"):
            rows.append(_skip_row("missing or invalid CdtDbtInd"))
            continue
        rows.append(
            CanonicalRow(
                date=date,
                direction="IN" if dc == "CRDT" else "OUT",
                amount=abs(amount),
                # Per-row currency rides through — the decision flow routes each row to the
                # wallet holding its currency (the legacy path force-booked the chosen
                # account's currency, silently corrupting cross-currency magnitudes).
                currency=(txn.get("currency") or "").upper(),
                description=(txn.get("description") or "").strip(),
                reference=(txn.get("ref") or "").strip() or None,
                external_refs=[
                    {
                        "source": import_camt.EXTERNAL_SOURCE,
                        "ref": import_camt._external_ref(acct_in_file, txn, amount),
                    }
                ],
            )
        )
    return rows


def _camt_canonical(content: bytes) -> ParseResult:
    acct_in_file, raw = import_camt.parse_camt(content)
    return ParseResult(
        meta=_camt_meta(content), rows=_camt_rows(raw, acct_in_file), tier="structured"
    )


_ADAPTERS = {
    "ofx": _ofx_canonical,
    "qif": _qif_canonical,
    "mt940": _mt940_canonical,
    "camt": _camt_canonical,
}


def to_canonical(content: bytes, fmt: str) -> ParseResult:
    """Parse a structured format deterministically into the canonical contract and run the
    D4 guards (the reconcile identity — MT940/CAMT declare opening+closing, so a clean file
    proves itself). Raises the format's own ImportError_ subclass on an unreadable file."""
    adapter = _ADAPTERS.get(fmt)
    if adapter is None:
        raise StatementParseError(f"not a structured import format: {fmt!r}")
    result = apply_guards(adapter(content))
    # Zero-LLM, deterministic, every record in the file parsed — complete by construction,
    # same reasoning as tier1_tabular's own stamp (decisions._parse_withheld_reason's
    # completeness gate).
    result.meta.extra["parse_complete"] = True
    return result
