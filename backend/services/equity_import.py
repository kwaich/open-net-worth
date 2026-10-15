"""Appendix 8B PDF import — equity-compensation backfill (feat/equity-comp, L6).

A user drops their employer's annual IRAS Appendix 8B ESOW statement. This module
extracts rows via the LLM seam (pypdf text → core.llm_seam.complete + parse_json →
strict schema), groups them into proposed GRANTS (by instrument + grant_date) and
proposed VEST EVENTS, and returns a preview payload the caller must echo back to
commit.

DESIGN PRINCIPLES (mirror services/import_pdf.py):
- Preview is PURE: no writes, no side effects, PDF bytes never persisted.
- Commit acts ONLY on the exact previewed payload the caller echoes back (no re-parse).
- LLM output is data, never instructions: parse_json → strict schema, unknown fields
  rejected, numbers clamped ≥ 0, dates validated as ISO YYYY-MM-DD.
- Rows below _MIN_CONFIDENCE are dropped, never fabricated.
- Re-importing the same PDF is a no-op: each vest event carries an idempotency key
  "8b:{sha256_of_file}:{row_index}" so apply_equity_change vest_apply returns the
  existing vest_event_id without a duplicate write.
- No network beyond the LLM seam. No instrument auto-creation.
- File-size cap mirrors import_pdf (_MAX_UPLOAD_BYTES = 25 MB from import_api.py).

Appendix 8B columns (per-row):
  company name, plan type (ESOP/ESOW), grant date, vest/exercise date,
  price paid per share (SGD), open market value per share at vest (SGD),
  number of shares, gross gain (SGD).

The document currency is SGD (Singapore dollars); fmv_currency defaults to "SGD"
unless the caller-supplied override differs.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import math
import re
import threading
from datetime import datetime

logger = logging.getLogger(__name__)

_MIN_CONFIDENCE = 0.6
_MAX_CHARS = 16_000
_MAX_UPLOAD_BYTES = 25 * 1024 * 1024  # 25 MB — mirrors import_api.py
_DEFAULT_CURRENCY = "SGD"
_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class EightBImportError(Exception):
    """A user-correctable problem with the Appendix 8B import."""


# ── PDF text extraction (mirrors import_pdf._extract_text) ────────────────────────


def _looks_like_pdf(content: bytes) -> bool:
    return b"%PDF-" in content[:1024]


def _extract_text(content: bytes) -> str:
    """Extract selectable text from the PDF via pypdf. Raises EightBImportError on
    an empty / non-PDF / image-only (unextractable) file."""
    if not content:
        raise EightBImportError("The file is empty.")
    if len(content) > _MAX_UPLOAD_BYTES:
        raise EightBImportError(f"File exceeds the {_MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit.")
    if not _looks_like_pdf(content):
        raise EightBImportError("This does not look like a PDF (no %PDF- marker).")
    try:
        import io

        from pypdf import PdfReader
    except ImportError:  # pragma: no cover — pypdf is a declared dependency
        raise EightBImportError("PDF support requires the pypdf package.")

    try:
        reader = PdfReader(io.BytesIO(content))
        pages = [(page.extract_text() or "") for page in reader.pages]
    except Exception as exc:
        raise EightBImportError(f"Could not read the PDF: {exc}")

    text = "\n".join(p for p in pages if p).strip()
    if not text:
        raise EightBImportError(
            "No selectable text found in the PDF (it may be a scanned/image statement). "
            "Export a text PDF or enter the vest events manually."
        )
    return text


# ── LLM extraction (through core.llm_seam — no ai import) ─────────────────────────


_PARSE_SYSTEM = (
    "You extract equity-compensation vest records from an IRAS Appendix 8B ESOW statement. "
    "Each row is one vest/exercise event. Be precise — extract ONLY completed vest/exercise rows. "
    "Return ONLY valid JSON, no prose."
)

_PARSE_PROMPT = """Extract every vest/exercise row from the Appendix 8B statement text below.

Return a JSON array (and nothing else):
[
  {{
    "company": "Company Name",
    "plan_type": "ESOW"|"ESOP",
    "grant_date": "YYYY-MM-DD",
    "vest_date": "YYYY-MM-DD",
    "price_paid_per_share": 0.00,
    "open_market_value": 0.00,
    "shares": 0.0,
    "gross_gain": 0.00,
    "confidence": 0.0
  }}
]

Column semantics:
- company: the employer company name (string).
- plan_type: "ESOW" (Employee Share Ownership plan) or "ESOP" (Employee Stock Option Plan). Default "ESOW" if unclear.
- grant_date: date the grant was originally awarded (YYYY-MM-DD). Convert any format.
- vest_date: the vest date, exercise date, or moratorium-lift date (YYYY-MM-DD). Convert any format. Infer the year from context.
- price_paid_per_share: the employee's cost/exercise price per share (SGD). 0 for RSUs with no cost.
- open_market_value: the open market value per share at the vest/exercise date (SGD).
- shares: number of shares vested/exercised (positive number).
- gross_gain: the taxable gain = shares × (open_market_value − price_paid_per_share) (SGD). Must be ≥ 0.
- confidence: 0.0–1.0 — how sure you are this is a real, correctly-read vest row. Use a LOW value if any field is ambiguous or the row may be a header/summary/total line.

Rules:
- Skip header rows, sub-total rows, grand-total rows, and any row that is not a vest/exercise event.
- All monetary values are in SGD unless the document states otherwise.
- gross_gain must be ≥ 0; if the raw figure is negative (price_paid > open_market_value), set gross_gain = 0.
- Do NOT invent values — if a field is unreadable, use a low confidence score.

Statement text:
{text}"""


async def _llm_extract(text: str) -> list[dict]:
    """Run the LLM extraction through core.llm_seam. Best-effort: returns [] on failure."""
    from backend.core.llm_seam import complete, parse_json

    prompt = _PARSE_PROMPT.format(text=text[:_MAX_CHARS])
    try:
        reply = await complete(
            prompt,
            system=_PARSE_SYSTEM,
            max_tokens=4096,
            source="equity_8b_parse",
            effort="none",  # 8-B document extraction — mechanical, no reasoning
        )
    except Exception as exc:
        logger.warning("equity_import: LLM parse failed — %s", exc)
        return []
    parsed = parse_json(reply)
    if not isinstance(parsed, list):
        logger.warning("equity_import: LLM parse returned non-list")
        return []
    return [item for item in parsed if isinstance(item, dict)]


def _run_async(coro) -> list[dict]:
    """Run an async coroutine from a sync context (mirrors import_pdf._run_async)."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    result: dict = {}

    def _worker() -> None:
        result["value"] = asyncio.run(coro)

    t = threading.Thread(target=_worker)
    t.start()
    t.join()
    return result.get("value", [])


# ── Row validation + coercion (strict schema; rejects unknown fields; clamps ≥ 0) ──


def _coerce_float(raw, *, min_val: float = 0.0) -> float | None:
    """Coerce to non-negative float; None if unusable."""
    if raw is None:
        return None
    try:
        val = float(raw)
    except (TypeError, ValueError):
        return None
    return max(min_val, val)


def _coerce_date(raw) -> str | None:
    """Coerce to ISO YYYY-MM-DD; None if unparseable."""
    if not raw:
        return None
    s = str(raw).strip()
    if _ISO_DATE_RE.match(s):
        try:
            datetime.strptime(s, "%Y-%m-%d")
            return s
        except ValueError:
            return None
    # Try common non-ISO formats as a fallback.
    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%m/%d/%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def _validate_row(raw: dict, row_index: int) -> dict:
    """Strict coercion + validation of one LLM row. Raises EightBImportError on
    any unusable / low-confidence / fabrication-risk row."""
    # Confidence gate (mirrors import_pdf — no-signal = pass, explicit low = drop).
    conf_raw = raw.get("confidence")
    if conf_raw is not None:
        try:
            conf = float(conf_raw)
        except (TypeError, ValueError):
            conf = None
        if conf is not None and conf < _MIN_CONFIDENCE:
            raise EightBImportError(f"row {row_index}: low confidence ({conf:.2f})")

    grant_date = _coerce_date(raw.get("grant_date"))
    if not grant_date:
        raise EightBImportError(f"row {row_index}: unparseable grant_date")

    vest_date = _coerce_date(raw.get("vest_date"))
    if not vest_date:
        raise EightBImportError(f"row {row_index}: unparseable vest_date")

    shares = _coerce_float(raw.get("shares"), min_val=0.0)
    if shares is None or shares <= 0:
        raise EightBImportError(f"row {row_index}: shares must be positive")

    fmv = _coerce_float(raw.get("open_market_value"), min_val=0.0)
    if fmv is None or fmv < 0:
        raise EightBImportError(f"row {row_index}: open_market_value must be ≥ 0")

    price_paid = _coerce_float(raw.get("price_paid_per_share"), min_val=0.0)
    if price_paid is None:
        price_paid = 0.0

    company = str(raw.get("company") or "").strip()
    if not company:
        raise EightBImportError(f"row {row_index}: missing company name")

    plan_type = str(raw.get("plan_type") or "ESOW").strip().upper()
    if plan_type not in ("ESOW", "ESOP"):
        plan_type = "ESOW"

    # gross_gain must be ≥ 0; recompute if not provided or negative.
    raw_gain = _coerce_float(raw.get("gross_gain"), min_val=0.0)
    gross_gain = round(shares * max(0.0, fmv - price_paid), 4) if raw_gain is None else raw_gain

    return {
        "company": company,
        "plan_type": plan_type,
        "grant_date": grant_date,
        "vest_date": vest_date,
        "price_paid_per_share": price_paid,
        "open_market_value": fmv,
        "shares": shares,
        "gross_gain": gross_gain,
    }


# ── File fingerprint (for idempotency keys) ───────────────────────────────────────


def _file_sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()[:32]


# ── Instrument resolution (fuzzy name match; NEVER auto-create) ───────────────────


def _resolve_instrument(company: str, instrument_id: str | None) -> dict | None:
    """Resolve instrument: explicit instrument_id wins; fall back to a fuzzy name/symbol
    match against existing instruments. Returns the instrument dict or None."""
    from backend.db.instrument_store import InstrumentStore

    store = InstrumentStore()

    if instrument_id:
        inst = store.find_by_id(instrument_id)
        if inst:
            return inst

    if not company:
        return None

    company_upper = company.upper()
    # Try exact symbol match first.
    by_symbol = store.find_by_symbol(company_upper)
    if by_symbol:
        return by_symbol

    # Fuzzy: case-insensitive substring of name or symbol.
    for inst in store.all():
        name = (inst.get("name") or "").upper()
        sym = (inst.get("symbol") or "").upper()
        if company_upper in name or company_upper in sym:
            return inst
        if name and name in company_upper:
            return inst
    return None


# ── Grant matching (by instrument + grant_date; avoid duplicate grants) ───────────


def _find_existing_grant(instrument_id: str, grant_date: str) -> dict | None:
    """Return the first active/fully_vested grant for this instrument + grant_date, or
    None when no match exists. Used to propose vest events against an existing grant
    rather than duplicating a grant that was already entered."""
    from backend.db.grant_store import GrantStore

    for grant in GrantStore().all():
        if (
            grant.get("instrument_id") == instrument_id
            and grant.get("grant_date") == grant_date
            and grant.get("status") in ("active", "fully_vested")
        ):
            return grant
    return None


def _find_matching_tranche(grant: dict, vest_date: str, units: float) -> dict | None:
    """Find an unvested tranche in the grant whose vest_date + units match (±0.01)."""
    for t in grant.get("tranches") or []:
        if t.get("vested"):
            continue
        if t.get("vest_date") == vest_date and abs(float(t.get("units") or 0) - units) <= 0.01:
            return t
    return None


# ── Core extraction + grouping logic ─────────────────────────────────────────────


def _group_rows(
    valid_rows: list[dict],
    file_sha: str,
    instrument_id_override: str | None,
    document_currency: str,
) -> dict:
    """Group validated rows into proposed grants and proposed vest events.

    Returns:
        {
          "rows_extracted": int,
          "rows_dropped": int,
          "proposed_grants": [{...}],    # new grants to create
          "proposed_vests": [{...}],     # vest events (against existing or proposed grants)
          "unresolved_rows": [{...}],    # rows with no resolvable instrument
          "document_currency": str,
        }
    """
    # Key: (instrument_id or company, grant_date) → {"instrument", "rows": []}
    grant_buckets: dict[tuple, dict] = {}
    unresolved: list[dict] = []

    for i, row in enumerate(valid_rows):
        company = row["company"]
        grant_date = row["grant_date"]

        inst = _resolve_instrument(company, instrument_id_override)
        if inst is None:
            unresolved.append(
                {"row_index": i, "company": company, "reason": "no matching instrument"}
            )
            continue

        key = (inst["id"], grant_date)
        if key not in grant_buckets:
            existing_grant = _find_existing_grant(inst["id"], grant_date)
            grant_buckets[key] = {
                "instrument": inst,
                "grant_date": grant_date,
                "existing_grant": existing_grant,
                "rows": [],
            }
        grant_buckets[key]["rows"].append((i, row))

    proposed_grants: list[dict] = []
    proposed_vests: list[dict] = []

    for (instrument_id, grant_date), bucket in grant_buckets.items():
        inst = bucket["instrument"]
        existing_grant = bucket["existing_grant"]
        rows = bucket["rows"]

        vest_rows = []
        for row_index, row in rows:
            idempotency_key = f"8b:{file_sha}:{row_index}"
            vest_entry = {
                "row_index": row_index,
                "vest_date": row["vest_date"],
                "units": row["shares"],
                "fmv_per_unit": row["open_market_value"],
                "fmv_currency": document_currency,
                "price_paid_per_unit": row["price_paid_per_share"],
                "gross_gain": row["gross_gain"],
                "idempotency_key": idempotency_key,
                "plan_type": row["plan_type"],
                "company": row["company"],
                # Always include instrument_id + grant_date so commit can resolve
                # the newly-created grant_id via new_grant_ids[(instrument_id, grant_date)].
                "instrument_id": instrument_id,
                "grant_date": grant_date,
            }
            if existing_grant:
                # Try to match to an existing unvested tranche.
                tranche = _find_matching_tranche(existing_grant, row["vest_date"], row["shares"])
                vest_entry["grant_id"] = existing_grant["id"]
                vest_entry["tranche_id"] = tranche["id"] if tranche else None
                vest_entry["against_existing_grant"] = True
                vest_entry["tranche_matched"] = tranche is not None
            else:
                vest_entry["against_existing_grant"] = False
                vest_entry["tranche_id"] = None  # resolved during commit

            vest_rows.append(vest_entry)

        if existing_grant is None:
            # Propose a new grant; tranches are the vest rows themselves.
            total_units = sum(r["shares"] for _, r in rows)
            grant_entry = {
                "instrument_id": instrument_id,
                "symbol": inst.get("symbol") or inst.get("name") or "",
                "grant_date": grant_date,
                "total_units": total_units,
                "currency": document_currency,
                "grant_type": _plan_type_to_grant_type(rows[0][1]["plan_type"]),
                "tax_event": "at_vest",
                "company": rows[0][1]["company"],
                "proposed_tranches": [
                    {
                        "vest_date": r["vest_date"],
                        "units": r["shares"],
                        "kind": "time",
                    }
                    for _, r in rows
                ],
                "note": (
                    "Imported from Appendix 8B. "
                    "These tranches are historical vests — add future tranches manually."
                ),
            }
            proposed_grants.append(grant_entry)

        proposed_vests.extend(vest_rows)

    return {
        "rows_extracted": len(valid_rows),
        "rows_dropped": 0,  # caller computes from raw_count - valid count
        "proposed_grants": proposed_grants,
        "proposed_vests": proposed_vests,
        "unresolved_rows": unresolved,
        "document_currency": document_currency,
    }


def _plan_type_to_grant_type(plan_type: str) -> str:
    return "rsu" if plan_type == "ESOW" else "rsu"


# ── Public API: preview ────────────────────────────────────────────────────────────


def preview_8b_import(
    content: bytes,
    instrument_id: str | None = None,
    document_currency: str = _DEFAULT_CURRENCY,
) -> dict:
    """Dry-run Appendix 8B extraction. Writes nothing. Returns the full proposed payload.

    Args:
        content: PDF bytes.
        instrument_id: caller-supplied instrument id for the whole document. When
            provided, overrides the fuzzy company-name matching for ALL rows.
        document_currency: override for fmv_currency (default "SGD" — Appendix 8B
            is always in SGD; override only when the employer uses a different base).

    Returns:
        {
          "ok": True,
          "raw_count": int,            # rows the LLM extracted before any gate
          "rows_extracted": int,       # rows passing validation + confidence gate
          "rows_dropped": int,         # rows below confidence or with validation errors
          "proposed_grants": [...],    # new grants to create (none if all match existing)
          "proposed_vests": [...],     # vest events with idempotency_key per row
          "unresolved_rows": [...],    # rows with no matching instrument
          "document_currency": str,
          "file_sha256": str,          # for caller reference; also embedded in idem keys
        }
    """
    text = _extract_text(content)
    file_sha = _file_sha256(content)
    raw_rows = _run_async(_llm_extract(text))
    raw_count = len(raw_rows)

    valid_rows: list[dict] = []
    dropped = 0
    for i, raw in enumerate(raw_rows):
        try:
            validated = _validate_row(raw, i)
            valid_rows.append(validated)
        except EightBImportError as exc:
            logger.info("equity_import: skipped row %d — %s", i, exc)
            dropped += 1

    grouped = _group_rows(valid_rows, file_sha, instrument_id, document_currency.upper())
    grouped["rows_dropped"] += dropped
    grouped["raw_count"] = raw_count

    return {
        "ok": True,
        "file_sha256": file_sha,
        **grouped,
    }


# ── Public API: commit ─────────────────────────────────────────────────────────────


def commit_8b_import(payload: dict) -> dict:
    """Commit the exactly-previewed payload: create proposed grants then apply vest
    events through apply_equity_change. Idempotent — re-committing the same payload
    (same idempotency_key per row) is a no-op that returns the original vest_event_ids.

    Args:
        payload: the dict returned by preview_8b_import (caller echoes it back).
            Must contain "proposed_grants" and "proposed_vests". Any extra field is
            ignored (no LLM output re-parsed here — the payload is user-echoed data).

    Returns:
        {
          "ok": True,
          "grants_created": int,
          "vests_applied": int,
          "vests_idempotent": int,
          "errors": [...],
        }
    """
    from backend.services import equity as equity_service

    if not isinstance(payload, dict):
        raise EightBImportError("commit payload must be a dict (the preview result echoed back)")

    proposed_grants: list[dict] = payload.get("proposed_grants") or []
    proposed_vests: list[dict] = payload.get("proposed_vests") or []

    # Map (symbol, grant_date) → freshly created grant_id so vest rows can reference them.
    new_grant_ids: dict[tuple, str] = {}
    grants_created = 0
    errors: list[dict] = []

    # Step 1: create proposed grants through the choke point.
    for g in proposed_grants:
        instrument_id = str(g.get("instrument_id") or "")
        grant_date = str(g.get("grant_date") or "")
        if not instrument_id or not grant_date:
            errors.append({"context": "grant", "error": "missing instrument_id or grant_date"})
            continue

        tranches = g.get("proposed_tranches") or []
        total_units = float(g.get("total_units") or sum(t.get("units", 0) for t in tranches))

        try:
            result = equity_service.apply_equity_change(
                {
                    "type": "grant_create",
                    "instrument_id": instrument_id,
                    "grant_date": grant_date,
                    "total_units": total_units,
                    "currency": str(g.get("currency") or "SGD"),
                    "grant_type": str(g.get("grant_type") or "rsu"),
                    "tax_event": str(g.get("tax_event") or "at_vest"),
                    "tranches": tranches,
                    "note": str(g.get("note") or ""),
                    "account_id": g.get("account_id") or None,
                    "account_name": g.get("account_name") or None,
                    "source": "import",
                }
            )
            gid = result.get("grant_id")
            if gid:
                new_grant_ids[(instrument_id, grant_date)] = gid
                grants_created += 1
        except Exception as exc:
            errors.append({"context": "grant_create", "grant_date": grant_date, "error": str(exc)})

    # Step 2: apply vest events through the choke point.
    vests_applied = 0
    vests_idempotent = 0

    for vest in proposed_vests:
        grant_id = vest.get("grant_id")
        vest_instrument_id = str(vest.get("instrument_id") or "")
        vest_grant_date = str(vest.get("grant_date") or "")

        # For vests referencing a newly-created grant, resolve grant_id now.
        if not grant_id and vest_instrument_id and vest_grant_date:
            grant_id = new_grant_ids.get((vest_instrument_id, vest_grant_date))

        if not grant_id:
            errors.append(
                {
                    "context": "vest_apply",
                    "row_index": vest.get("row_index"),
                    "error": "could not determine grant_id",
                }
            )
            continue

        # Locate the tranche: if pre-matched, use tranche_id; otherwise find it on
        # the grant by vest_date + units.
        tranche_id = vest.get("tranche_id")
        if not tranche_id:
            from backend.db.grant_store import GrantStore

            grant_obj = GrantStore().find_by_id(grant_id)
            if grant_obj:
                t = _find_matching_tranche(
                    grant_obj, str(vest.get("vest_date") or ""), float(vest.get("units") or 0)
                )
                if t:
                    tranche_id = t["id"]

        if not tranche_id:
            errors.append(
                {
                    "context": "vest_apply",
                    "row_index": vest.get("row_index"),
                    "grant_id": grant_id,
                    "error": "no matching unvested tranche found; vest may already be recorded",
                }
            )
            continue

        idem_key = str(vest.get("idempotency_key") or "")

        # Defense-in-depth: validate numeric fields before hitting the choke point.
        # The preview validator clamps values, but the caller echoes the payload and
        # could have tampered with it.
        try:
            _fmv = float(vest.get("fmv_per_unit") or 0)
            _pppu = float(vest.get("price_paid_per_unit") or 0.0)
            _units = float(vest.get("units") or 0)
        except (TypeError, ValueError) as exc:
            errors.append(
                {
                    "context": "vest_apply",
                    "row_index": vest.get("row_index"),
                    "grant_id": grant_id,
                    "tranche_id": tranche_id,
                    "error": f"non-numeric vest field: {exc}",
                }
            )
            continue

        if not math.isfinite(_fmv) or _fmv < 0:
            errors.append(
                {
                    "context": "vest_apply",
                    "row_index": vest.get("row_index"),
                    "grant_id": grant_id,
                    "tranche_id": tranche_id,
                    "error": "fmv_per_unit must be a finite number ≥ 0",
                }
            )
            continue
        if not math.isfinite(_pppu) or _pppu < 0:
            errors.append(
                {
                    "context": "vest_apply",
                    "row_index": vest.get("row_index"),
                    "grant_id": grant_id,
                    "tranche_id": tranche_id,
                    "error": "price_paid_per_unit must be a finite number ≥ 0",
                }
            )
            continue

        try:
            result = equity_service.apply_equity_change(
                {
                    "type": "vest_apply",
                    "grant_id": grant_id,
                    "tranche_id": tranche_id,
                    "units": _units,
                    "fmv_per_unit": _fmv,
                    "fmv_currency": str(vest.get("fmv_currency") or "SGD"),
                    "vest_date": str(vest.get("vest_date") or ""),
                    "price_paid_per_unit": _pppu,
                    "idempotency_key": idem_key,
                    "source": "import",
                }
            )
            if result.get("idempotent"):
                vests_idempotent += 1
            else:
                vests_applied += 1
        except Exception as exc:
            errors.append(
                {
                    "context": "vest_apply",
                    "row_index": vest.get("row_index"),
                    "grant_id": grant_id,
                    "tranche_id": tranche_id,
                    "error": str(exc),
                }
            )

    return {
        "ok": True,
        "grants_created": grants_created,
        "vests_applied": vests_applied,
        "vests_idempotent": vests_idempotent,
        "errors": errors,
    }
