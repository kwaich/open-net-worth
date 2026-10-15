"""
Equity-compensation REST API (feat/equity-comp).

A THIN router over services.equity and services.equity_report — validates input,
calls ONE service function, and shapes the response.  All business logic and monetary
math stays in the service layer; the router never touches stores or computes amounts.

Dependency rule: api → services only.  No db/core imports used for logic.

Routes (RFC §8 / §14 — the frontend is built against these exact paths):
    GET    /api/equity/grants                                   — enriched grant list
    POST   /api/equity/grants                                   → grant_create
    PATCH  /api/equity/grants/{grant_id}                        → grant_update
    POST   /api/equity/grants/{grant_id}/cancel                 → grant_cancel
    POST   /api/equity/grants/{grant_id}/tranches/{tranche_id}/vest  → vest_apply
    POST   /api/equity/vest-events/{vest_event_id}/undo         → vest_undo
    GET    /api/equity/pending-releases                         — vested, not yet delivered
    GET    /api/equity/vest-history                             — recorded vests + release state
    GET    /api/equity/report                                   — per-year vest report
    GET    /api/equity/upcoming                                 — vest calendar
    GET    /api/equity/grants/{grant_id}/tranches/{tranche_id}/fmv  — FMV prefill

Appendix 8B PDF import routes (L6 — stretch lane):
    POST   /api/equity/import/8b/preview                        — dry-run (no writes)
    POST   /api/equity/import/8b/commit                         — apply previewed payload
"""

from __future__ import annotations

import asyncio
from typing import Optional

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile

from backend.api._deps import _bearer_token
from backend.core.errors import OpenNetWorthError, to_http_status
from backend.models.equity import GrantCreate, GrantUpdate, ReleaseApply, VestApply
from backend.services import equity as equity_service
from backend.services import equity_release as equity_release_service
from backend.services.equity_report import equity_report

router = APIRouter()


# ── Grant reads ─────────────────────────────────────────────────────────────────────


@router.get("/api/equity/grants")
def list_grants():
    """All grants with computed symbol, vested/unvested unit counts, next vest date,
    and tranche status.  Computed server-side so the frontend gets a ready-to-render
    list without any derivation."""
    return {"grants": equity_service.hydrated_grants()}


# ── Grant writes ────────────────────────────────────────────────────────────────────


@router.post("/api/equity/grants")
def create_grant(body: GrantCreate):
    """Create a new equity grant and its locked position (no money movement).
    The grant type (RSU/LTIP) is passed as ``grant_type`` to the choke point because
    ``type`` is the dispatcher key."""
    change = {
        "type": "grant_create",
        "grant_type": body.type.value if hasattr(body.type, "value") else body.type,
        "instrument_id": body.instrument_id,
        "grant_date": body.grant_date,
        "total_units": body.total_units,
        "currency": body.currency,
        "grant_price_per_unit": body.grant_price_per_unit,
        "tax_event": body.tax_event.value if hasattr(body.tax_event, "value") else body.tax_event,
        "release_required": body.release_required,
        "account_id": body.account_id,
        "account_name": body.account_name,
        "locked_position_id": body.locked_position_id,
        "tranches": body.tranches,
        "note": body.note,
        "source": "manual",
    }
    try:
        return equity_service.apply_equity_change(change)
    except OpenNetWorthError as exc:
        raise HTTPException(to_http_status(exc), exc.message or str(exc)) from exc


@router.patch("/api/equity/grants/{grant_id}")
def update_grant(grant_id: str, body: GrantUpdate):
    """Update mutable grant fields (grant_date, currency, tax_event, note, tranches,
    account_id/account_name). Passing account_id or account_name moves the grant and
    its locked position to the target account in one step.
    Immutable fields (instrument_id, total_units) are rejected (extra=forbid → 422)."""
    change: dict = {"type": "grant_update", "grant_id": grant_id}
    if body.grant_date is not None:
        change["grant_date"] = body.grant_date
    if body.currency is not None:
        change["currency"] = body.currency
    if body.grant_price_per_unit is not None:
        change["grant_price_per_unit"] = body.grant_price_per_unit
    if body.tax_event is not None:
        change["tax_event"] = (
            body.tax_event.value if hasattr(body.tax_event, "value") else body.tax_event
        )
    if body.release_required is not None:
        change["release_required"] = body.release_required
    if body.note is not None:
        change["note"] = body.note
    if body.tranches is not None:
        change["tranches"] = body.tranches
    if body.account_id is not None:
        change["account_id"] = body.account_id
    if body.account_name is not None:
        change["account_name"] = body.account_name
    try:
        return equity_service.apply_equity_change(change)
    except OpenNetWorthError as exc:
        raise HTTPException(to_http_status(exc), exc.message or str(exc)) from exc


@router.post("/api/equity/grants/{grant_id}/cancel")
def cancel_grant(grant_id: str):
    """Mark a grant cancelled.  Does not reverse any vest events."""
    try:
        return equity_service.apply_equity_change({"type": "grant_cancel", "grant_id": grant_id})
    except OpenNetWorthError as exc:
        raise HTTPException(to_http_status(exc), exc.message or str(exc)) from exc


# ── Vest writes ──────────────────────────────────────────────────────────────────────


@router.post("/api/equity/grants/{grant_id}/tranches/{tranche_id}/vest")
def vest_tranche(grant_id: str, tranche_id: str, body: VestApply):
    """Apply a vest: FMV lot (or park the units as pending) + locked-position decrement +
    vest event. Writes NO ledger transaction — a vest delivers shares, not cash. The
    action is choke-pointed through apply_equity_change so every write is checkpoint-covered.  Idempotent: re-applying the same idempotency_key is a
    no-op that returns the original vest_event_id."""
    change = {
        "type": "vest_apply",
        "grant_id": grant_id,
        "tranche_id": tranche_id,
        "units": body.units,
        "fmv_per_unit": body.fmv_per_unit,
        "fmv_currency": body.fmv_currency,
        "vest_date": body.vest_date,
        "price_paid_per_unit": body.price_paid_per_unit,
        "units_withheld": body.units_withheld,
        "expected_release_date": body.expected_release_date,
        "idempotency_key": body.idempotency_key,
        "source": body.source,
        "note": body.note,
    }
    try:
        return equity_service.apply_equity_change(change)
    except OpenNetWorthError as exc:
        raise HTTPException(to_http_status(exc), exc.message or str(exc)) from exc


@router.post("/api/equity/vest-events/{vest_event_id}/release")
def release_vest(vest_event_id: str, body: ReleaseApply):
    """Record delivery of a vest. Current two-step vests move pending units onto an FMV
    lot; legacy vests that already have a lot annotate the release date only. No
    transaction is written and vest-date basis is unchanged. 409 if already released."""
    change = {
        "type": "release_apply",
        "vest_event_id": vest_event_id,
        "release_date": body.release_date,
        "note": body.note,
    }
    try:
        return equity_service.apply_equity_change(change)
    except OpenNetWorthError as exc:
        raise HTTPException(to_http_status(exc), exc.message or str(exc)) from exc


@router.get("/api/equity/pending-releases")
def get_pending_releases(grant_id: Optional[str] = None):
    """Vests whose delivery has not been recorded yet, soonest-expected first. Each item
    carries {vest_event_id, grant_id, tranche_id, symbol, vest_date, units, fmv_per_unit,
    expected_release_date, overdue, indicative_value_base}.  ``overdue`` is only ever true
    when an expected date was actually recorded — a blank one means unknown, never late."""
    return {"items": equity_release_service.pending_releases(grant_id=grant_id)}


@router.get("/api/equity/vest-history")
def get_vest_history():
    """Persisted vest events, newest first, with event-derived release state and the
    display fields shared by the pending-release flow."""
    return equity_service.vest_history()


@router.post("/api/equity/vest-events/{vest_event_id}/undo")
def undo_vest(vest_event_id: str):
    """Reverse a vest event — re-increments locked units, deletes the FMV lot (or drains
    the pending units), flips the tranche back. Also soft-deletes the income txn of a vest
    recorded before 2026-09-22, when a vest still booked one.  Refused with
    409 if the lot has already been partially disposed."""
    try:
        return equity_service.apply_equity_change(
            {"type": "vest_undo", "vest_event_id": vest_event_id}
        )
    except OpenNetWorthError as exc:
        raise HTTPException(to_http_status(exc), exc.message or str(exc)) from exc


# ── Report + calendar + FMV prefill ─────────────────────────────────────────────────


@router.get("/api/equity/report")
def get_equity_report(year: Optional[int] = None):
    """Per-vest report for a year.  ``year`` defaults to the current year.
    Returns: {year, years, base_currency, events, total_gain_base}.
    ``years`` lists every year with ≥1 vest event (for the frontend year picker)."""
    return equity_report(year=year)


@router.get("/api/equity/upcoming")
def get_upcoming_vests(window_days: Optional[int] = None):
    """Upcoming unvested tranches sorted by date — ALL future tranches by default;
    pass ``window_days`` to restrict to a window.  Each item carries {grant_id,
    tranche_id, symbol, vest_date, units, indicative_value_base, overdue}."""
    items = equity_service.upcoming_vests(window_days=window_days)
    return {"items": items}


@router.get("/api/equity/grants/{grant_id}/tranches/{tranche_id}/fmv")
def get_fmv_prefill(grant_id: str, tranche_id: str):
    """FMV prefill for the vest form — returns the price at/before the tranche's
    vest_date from price history.  {fmv_per_unit, fmv_currency, as_of, source}.
    ``fmv_per_unit=null, source='none'`` when no price is available."""
    try:
        return equity_service.fmv_prefill(grant_id=grant_id, tranche_id=tranche_id)
    except OpenNetWorthError as exc:
        raise HTTPException(to_http_status(exc), exc.message or str(exc)) from exc


# ── Appendix 8B PDF import (L6 — stretch lane) ──────────────────────────────────


def _require_csrf(request: Request) -> None:
    """Mirror import_api.py's export-style CSRF guard: a Bearer token OR an
    ``X-Requested-With`` header. Neither → 403."""
    has_bearer = _bearer_token(request) is not None
    requested_with = request.headers.get("x-requested-with", "").strip().lower()
    if not has_bearer and requested_with != "xmlhttprequest":
        raise HTTPException(403, "missing X-Requested-With header")


@router.post("/api/equity/import/8b/preview")
async def preview_8b_import(
    request: Request,
    file: UploadFile = File(...),
    instrument_id: Optional[str] = Form(None),
    document_currency: str = Form("SGD"),
) -> dict:
    """Dry-run Appendix 8B import: extract rows via the LLM seam, group into proposed
    grants and vest events, and return the proposed payload — writes NOTHING.

    The caller must echo this payload back to /api/equity/import/8b/commit to apply it.
    This is the propose-confirm split: the user reviews the proposals before any write.

    Multipart form fields:
      - ``file``: the Appendix 8B PDF (required; max 25 MB).
      - ``instrument_id``: optional; pins the instrument for all rows in the document
        (skips the fuzzy company-name match — recommended when the company name in the PDF
        differs from the instrument symbol/name in the system).
      - ``document_currency``: override the document currency (default "SGD" — Appendix 8B
        is always in Singapore dollars; change only when the employer uses a different base).

    Returns: {ok, raw_count, rows_extracted, rows_dropped, proposed_grants,
    proposed_vests, unresolved_rows, document_currency, file_sha256}.
    """
    _require_csrf(request)
    from backend.services.equity_import import (  # noqa: PLC0415
        _MAX_UPLOAD_BYTES,
        EightBImportError,
    )
    from backend.services.equity_import import (
        preview_8b_import as _preview,
    )

    # Reject via Content-Length before buffering the body when the header is present.
    cl = request.headers.get("content-length")
    if cl is not None:
        try:
            if int(cl) > _MAX_UPLOAD_BYTES:
                raise HTTPException(413, "file too large")
        except ValueError:
            pass  # malformed Content-Length — let the streaming check below catch it

    # Stream-read the UploadFile in chunks so we never buffer more than the cap.
    chunks: list[bytes] = []
    total = 0
    chunk_size = 64 * 1024  # 64 KB
    while True:
        chunk = await file.read(chunk_size)
        if not chunk:
            break
        total += len(chunk)
        if total > _MAX_UPLOAD_BYTES:
            raise HTTPException(413, "file too large")
        chunks.append(chunk)

    content = b"".join(chunks)
    if not content:
        raise HTTPException(422, "empty upload")
    try:
        return await asyncio.to_thread(
            _preview,
            content,
            instrument_id=instrument_id or None,
            document_currency=document_currency or "SGD",
        )
    except EightBImportError as exc:
        raise HTTPException(422, str(exc)) from exc
    except OpenNetWorthError as exc:
        raise HTTPException(to_http_status(exc), exc.message or str(exc)) from exc


@router.post("/api/equity/import/8b/commit")
async def commit_8b_import(
    request: Request,
    payload: dict,
) -> dict:
    """Apply the proposed Appendix 8B payload (the exact dict returned by the preview
    route, echoed back by the caller after user confirmation).

    SECURITY: acts only on the caller-echoed payload — no PDF re-parsing, no LLM call.
    Each vest event carries a deterministic idempotency_key ("8b:{sha256}:{row_index}"),
    so re-committing the same payload is a safe no-op.

    Returns: {ok, grants_created, vests_applied, vests_idempotent, errors}.
    """
    _require_csrf(request)
    from backend.services.equity_import import EightBImportError
    from backend.services.equity_import import commit_8b_import as _commit

    try:
        return _commit(payload)
    except EightBImportError as exc:
        raise HTTPException(422, str(exc)) from exc
    except OpenNetWorthError as exc:
        raise HTTPException(to_http_status(exc), exc.message or str(exc)) from exc
