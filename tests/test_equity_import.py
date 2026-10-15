"""Unit + smoke tests for backend/services/equity_import.py (feat/equity-comp L6).

The real LLM call is mocked by monkeypatching core.llm_seam._complete (same
technique as test_import_pdf_service.py). All stores isolated to tmp_path.

Tests cover:
- Extraction: real pypdf text extraction; empty/non-PDF/textless raise.
- LLM parse: happy path (2 grants × multiple rows); LLM failure → no crash.
- Confidence gate: low-confidence rows dropped, not fabricated.
- Validation: bad dates / negative amounts / missing company rejected.
- Existing-grant matching: no duplicate grant proposed when one already exists.
- Commit: grants + vests created through the choke point with correct amounts/dates.
- Idempotency: re-committing same payload is a no-op (vests_idempotent == count).
- Instrument resolution: fuzzy name match; unresolved rows reported cleanly.
- File-size cap: oversized bytes rejected before LLM call.
- Malformed LLM output: non-list / unknown fields handled without crash.
- API routes: preview 200 + shape; commit 200 + round-trip; CSRF guard.
"""

from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.equity import router
from backend.db.account_store import AccountStore
from backend.db.grant_store import GrantStore
from backend.db.instrument_store import InstrumentStore
from backend.db.position_store import PositionStore
from backend.db.transaction_store import TransactionStore
from backend.db.vest_event_store import VestEventStore
from backend.services import equity as equity_svc
from backend.services import equity_import as svc
from backend.services import equity_report as report_svc
from backend.services import ledger as ledger_service
from backend.services import lots as lots_service

# ── Fixtures ───────────────────────────────────────────────────────────────────────


@pytest.fixture
def isolated(tmp_path, monkeypatch, db_isolated):
    """Redirect every backing file to tmp_path + identity FX (SGD base, 1:1)."""
    # accounts/transactions are DB-backed now (plan 3 task 4) — db_isolated (above) redirects
    # the process-wide DB singleton, so the fresh AccountStore()/TransactionStore() instances
    # wired below resolve against it with no per-instance path-sync needed.

    monkeypatch.setattr(equity_svc, "grants", GrantStore())
    monkeypatch.setattr(equity_svc, "vest_events", VestEventStore())
    monkeypatch.setattr(equity_svc, "instruments", InstrumentStore())
    monkeypatch.setattr(equity_svc, "positions", PositionStore())
    monkeypatch.setattr(lots_service, "instruments", InstrumentStore())
    monkeypatch.setattr(ledger_service, "accounts", AccountStore())
    monkeypatch.setattr(ledger_service, "transactions", TransactionStore())

    monkeypatch.setattr(equity_svc, "base_currency", lambda: "SGD")
    monkeypatch.setattr(lots_service, "base_currency", lambda: "SGD")
    monkeypatch.setattr(ledger_service, "base_currency", lambda: "SGD")
    monkeypatch.setattr("backend.core.fx.base_currency", lambda: "SGD")
    rates = {"SGD": 1.0}
    monkeypatch.setattr(lots_service, "_load_rates", lambda: rates)
    monkeypatch.setattr(
        "backend.services.ledger.to_base",
        lambda a, c, b=None, rates=None: round(float(a or 0), 2),
    )
    monkeypatch.setattr(
        equity_svc,
        "to_base",
        lambda a, c, b=None, rates=None: round(float(a or 0), 2),
    )

    return tmp_path


@pytest.fixture
def client(isolated, monkeypatch):
    """TestClient wired to the isolated stores + identity FX."""
    monkeypatch.setattr(report_svc, "grants", GrantStore())
    monkeypatch.setattr(report_svc, "vest_events", VestEventStore())
    monkeypatch.setattr(report_svc, "instruments", InstrumentStore())
    monkeypatch.setattr(report_svc, "transactions", TransactionStore())

    app = FastAPI()
    app.include_router(router)
    return TestClient(app, headers={"X-Requested-With": "XMLHttpRequest"})


# ── PDF helpers ────────────────────────────────────────────────────────────────────


def _make_pdf(text: str) -> bytes:
    """Build a real text PDF via pymupdf so pypdf text-extraction is exercised."""
    import fitz

    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), text)
    data = doc.tobytes()
    doc.close()
    return data


_SAMPLE_PDF = _make_pdf(
    "IRAS Appendix 8B ESOW Statement\n"
    "Company  PlanType  GrantDate  VestDate  PricePaid  OMV  Shares  GrossGain\n"
    "ONWAI Corp  ESOW  2023-03-01  2024-03-01  0.00  30.00  300  9000.00\n"
    "ONWAI Corp  ESOW  2023-03-01  2025-03-01  0.00  32.00  300  9600.00\n"
    "ALPHA Corp  ESOW  2022-06-01  2024-06-01  5.00  25.00  200  4000.00\n"
)

# Two grants x multiple rows — the LLM stub returns these.
_GOOD_ROWS = [
    {
        "company": "ONWAI Corp",
        "plan_type": "ESOW",
        "grant_date": "2023-03-01",
        "vest_date": "2024-03-01",
        "price_paid_per_share": 0.0,
        "open_market_value": 30.0,
        "shares": 300.0,
        "gross_gain": 9000.0,
        "confidence": 0.95,
    },
    {
        "company": "ONWAI Corp",
        "plan_type": "ESOW",
        "grant_date": "2023-03-01",
        "vest_date": "2025-03-01",
        "price_paid_per_share": 0.0,
        "open_market_value": 32.0,
        "shares": 300.0,
        "gross_gain": 9600.0,
        "confidence": 0.92,
    },
    {
        "company": "ALPHA Corp",
        "plan_type": "ESOW",
        "grant_date": "2022-06-01",
        "vest_date": "2024-06-01",
        "price_paid_per_share": 5.0,
        "open_market_value": 25.0,
        "shares": 200.0,
        "gross_gain": 4000.0,
        "confidence": 0.88,
    },
]


def _stub_llm(monkeypatch, rows: list[dict]):
    """Register a deterministic complete on the LLM seam (mirrors test_import_pdf_service)."""
    from backend.core import llm_seam

    async def _complete(prompt, **kwargs):
        return json.dumps(rows)

    monkeypatch.setattr(llm_seam, "_complete", _complete)
    monkeypatch.setattr(llm_seam, "_parse_json", None)  # falls back to json.loads


def _seed_inst(symbol: str, name: str) -> dict:
    return InstrumentStore().create(
        {
            "symbol": symbol,
            "name": name,
            "quote_currency": "SGD",
            "price_source_id": symbol,
            "price_source": "yahoo",
        }
    )


def _seed_acct() -> dict:
    return AccountStore().create(
        {"name": "Employer Equity", "kind": "asset", "type": "investment", "currency": "SGD"}
    )


# ── PDF text extraction ────────────────────────────────────────────────────────────


def test_extract_text_reads_real_pdf():
    text = svc._extract_text(_SAMPLE_PDF)
    assert "ONWAI Corp" in text


def test_extract_empty_raises():
    with pytest.raises(svc.EightBImportError, match="empty"):
        svc._extract_text(b"")


def test_extract_non_pdf_raises():
    with pytest.raises(svc.EightBImportError, match="PDF"):
        svc._extract_text(b"Date,Company,Amount\n2024-01-01,ONWAI,30.00\n")


def test_extract_oversized_raises():
    # Content just over 25 MB.
    big = b"%PDF-" + b"x" * (26 * 1024 * 1024)
    with pytest.raises(svc.EightBImportError, match="limit"):
        svc._extract_text(big)


def test_extract_textless_pdf_raises():
    blank = _make_pdf("")
    with pytest.raises(svc.EightBImportError, match="No selectable text"):
        svc._extract_text(blank)


# ── Row validation ─────────────────────────────────────────────────────────────────


def test_validate_row_good():
    row = svc._validate_row(_GOOD_ROWS[0], 0)
    assert row["company"] == "ONWAI Corp"
    assert row["shares"] == 300.0
    assert row["open_market_value"] == 30.0
    assert row["gross_gain"] == 9000.0
    assert row["grant_date"] == "2023-03-01"
    assert row["vest_date"] == "2024-03-01"


def test_validate_row_low_confidence_dropped():
    raw = {**_GOOD_ROWS[0], "confidence": 0.3}
    with pytest.raises(svc.EightBImportError, match="confidence"):
        svc._validate_row(raw, 0)


def test_validate_row_missing_confidence_passes():
    raw = {k: v for k, v in _GOOD_ROWS[0].items() if k != "confidence"}
    row = svc._validate_row(raw, 0)
    assert row["company"] == "ONWAI Corp"


def test_validate_row_bad_grant_date():
    raw = {**_GOOD_ROWS[0], "grant_date": "not-a-date"}
    with pytest.raises(svc.EightBImportError, match="grant_date"):
        svc._validate_row(raw, 0)


def test_validate_row_bad_vest_date():
    raw = {**_GOOD_ROWS[0], "vest_date": ""}
    with pytest.raises(svc.EightBImportError, match="vest_date"):
        svc._validate_row(raw, 0)


def test_validate_row_zero_shares():
    raw = {**_GOOD_ROWS[0], "shares": 0}
    with pytest.raises(svc.EightBImportError, match="shares"):
        svc._validate_row(raw, 0)


def test_validate_row_missing_company():
    raw = {**_GOOD_ROWS[0], "company": ""}
    with pytest.raises(svc.EightBImportError, match="company"):
        svc._validate_row(raw, 0)


def test_validate_row_negative_gross_gain_clamped():
    # Negative gross_gain should be recalculated as max(0, omv-paid) * shares.
    raw = {
        **_GOOD_ROWS[0],
        "gross_gain": -100.0,
        "open_market_value": 10.0,
        "price_paid_per_share": 20.0,
    }
    row = svc._validate_row(raw, 0)
    # shares=300, omv=10, paid=20 → max(0, -10)*300 = 0; but gross_gain raw is negative → recalculate
    assert row["gross_gain"] == pytest.approx(0.0)


def test_validate_row_date_coercion():
    # Various non-ISO date formats should be accepted.
    raw = {**_GOOD_ROWS[0], "grant_date": "01/03/2023", "vest_date": "01/03/2024"}
    row = svc._validate_row(raw, 0)
    assert row["grant_date"] == "2023-03-01"
    assert row["vest_date"] == "2024-03-01"


# ── LLM extraction ─────────────────────────────────────────────────────────────────


def test_llm_failure_returns_empty_list(monkeypatch):
    """Provider failure must not crash — returns [] so preview reports zero rows."""
    from backend.core import llm_seam

    async def _boom(prompt, **kwargs):
        raise RuntimeError("provider down")

    monkeypatch.setattr(llm_seam, "_complete", _boom)
    result = svc._run_async(svc._llm_extract("some text"))
    assert result == []


def test_llm_non_list_returns_empty(monkeypatch):
    from backend.core import llm_seam

    async def _not_list(prompt, **kwargs):
        return json.dumps({"error": "unexpected"})

    monkeypatch.setattr(llm_seam, "_complete", _not_list)
    result = svc._run_async(svc._llm_extract("text"))
    assert result == []


# ── Preview (pure — writes nothing) ───────────────────────────────────────────────


def test_preview_writes_nothing(isolated, monkeypatch):
    _stub_llm(monkeypatch, _GOOD_ROWS)
    _seed_inst("ONWAI", "ONWAI Corp")
    _seed_inst("ALPHA", "ALPHA Corp")
    _seed_acct()

    result = svc.preview_8b_import(_SAMPLE_PDF, document_currency="SGD")
    assert result["ok"] is True

    # No writes occurred.
    assert GrantStore().all() == []
    assert VestEventStore().all() == []
    assert TransactionStore().all() == []


def test_preview_two_grants_multiple_rows(isolated, monkeypatch):
    _stub_llm(monkeypatch, _GOOD_ROWS)
    _seed_inst("ONWAI", "ONWAI Corp")
    _seed_inst("ALPHA", "ALPHA Corp")

    result = svc.preview_8b_import(_SAMPLE_PDF, document_currency="SGD")
    assert result["rows_extracted"] == 3
    assert result["rows_dropped"] == 0
    assert len(result["proposed_grants"]) == 2
    assert len(result["proposed_vests"]) == 3
    assert result["unresolved_rows"] == []
    assert result["document_currency"] == "SGD"
    assert result["file_sha256"]  # non-empty


def test_preview_low_confidence_row_dropped(isolated, monkeypatch):
    rows = [
        {**_GOOD_ROWS[0], "confidence": 0.9},
        {**_GOOD_ROWS[1], "confidence": 0.15},  # below _MIN_CONFIDENCE
    ]
    _stub_llm(monkeypatch, rows)
    _seed_inst("ONWAI", "ONWAI Corp")

    result = svc.preview_8b_import(_SAMPLE_PDF)
    assert result["rows_extracted"] == 1
    assert result["rows_dropped"] == 1


def test_preview_unresolved_instrument(isolated, monkeypatch):
    _stub_llm(monkeypatch, _GOOD_ROWS)
    # Only seed ONWAI — ALPHA should be unresolved.
    _seed_inst("ONWAI", "ONWAI Corp")

    result = svc.preview_8b_import(_SAMPLE_PDF)
    assert len(result["proposed_grants"]) == 1  # only ONWAI
    assert len(result["unresolved_rows"]) == 1
    assert result["unresolved_rows"][0]["company"] == "ALPHA Corp"


def test_preview_instrument_id_override(isolated, monkeypatch):
    """When instrument_id is given, all rows resolve to that instrument."""
    _stub_llm(monkeypatch, _GOOD_ROWS[:2])  # only ONWAI rows
    inst = _seed_inst("ONWAI", "ONWAI Corp")

    result = svc.preview_8b_import(_SAMPLE_PDF, instrument_id=inst["id"])
    assert result["rows_extracted"] == 2
    assert all(g["instrument_id"] == inst["id"] for g in result["proposed_grants"])


def test_preview_existing_grant_matching(isolated, monkeypatch):
    """When a matching grant already exists, no duplicate grant is proposed."""
    _stub_llm(monkeypatch, _GOOD_ROWS[:2])  # ONWAI, same grant_date
    inst = _seed_inst("ONWAI", "ONWAI Corp")
    acct = _seed_acct()

    # Pre-create the ONWAI grant with matching tranches.
    equity_svc.apply_equity_change(
        {
            "type": "grant_create",
            "instrument_id": inst["id"],
            "account_id": acct["id"],
            "grant_date": "2023-03-01",
            "total_units": 600.0,
            "tax_event": "at_vest",
            "tranches": [
                {"vest_date": "2024-03-01", "units": 300.0},
                {"vest_date": "2025-03-01", "units": 300.0},
            ],
        }
    )

    result = svc.preview_8b_import(_SAMPLE_PDF, instrument_id=inst["id"])
    assert result["proposed_grants"] == []  # no duplicate grant
    # vest events should still be proposed (against the existing grant).
    assert len(result["proposed_vests"]) == 2
    assert all(v["against_existing_grant"] is True for v in result["proposed_vests"])


# ── Commit (writes through choke points) ──────────────────────────────────────────


def _inject_account(preview: dict, account_id: str) -> dict:
    """Stamp account_id on every proposed grant so commit_8b_import has an account."""
    out = dict(preview)
    out["proposed_grants"] = [
        {**g, "account_id": account_id} for g in (preview.get("proposed_grants") or [])
    ]
    return out


def test_commit_happy_path(isolated, monkeypatch):
    _stub_llm(monkeypatch, _GOOD_ROWS[:2])  # ONWAI only
    inst = _seed_inst("ONWAI", "ONWAI Corp")
    acct = _seed_acct()

    preview = svc.preview_8b_import(_SAMPLE_PDF, instrument_id=inst["id"])
    result = svc.commit_8b_import(_inject_account(preview, acct["id"]))

    assert result["ok"] is True
    assert result["grants_created"] == 1
    assert result["vests_applied"] == 2
    assert result["vests_idempotent"] == 0
    assert result["errors"] == []

    # Verify DB state.
    grants = GrantStore().all()
    assert len(grants) == 1
    assert grants[0]["grant_date"] == "2023-03-01"

    vest_evs = VestEventStore().all()
    assert len(vest_evs) == 2
    dates = {ev["date"] for ev in vest_evs}
    assert "2024-03-01" in dates
    assert "2025-03-01" in dates

    # An imported vest books no ledger row either — same choke, same rule
    # (ADR 2026-09-22-a-vest-books-no-income-transaction).
    assert TransactionStore().all() == []


def test_commit_correct_amounts(isolated, monkeypatch):
    """Verify: units=300, fmv=30, paid=0 → gross_gain=9000, to_amount=9000."""
    _stub_llm(monkeypatch, [_GOOD_ROWS[0]])  # one row: 300 × 30 = 9000
    inst = _seed_inst("ONWAI", "ONWAI Corp")
    acct = _seed_acct()

    preview = svc.preview_8b_import(_SAMPLE_PDF, instrument_id=inst["id"])
    svc.commit_8b_import(_inject_account(preview, acct["id"]))

    assert TransactionStore().all() == []

    vest_evs = VestEventStore().all()
    assert len(vest_evs) == 1
    assert vest_evs[0]["fmv_per_unit"] == pytest.approx(30.0)
    assert vest_evs[0]["units"] == pytest.approx(300.0)
    assert vest_evs[0]["gross_gain_native"] == pytest.approx(9000.0)


def test_commit_correct_vest_date(isolated, monkeypatch):
    """Vest event must use the document's vest_date, not today."""
    _stub_llm(monkeypatch, [_GOOD_ROWS[0]])
    inst = _seed_inst("ONWAI", "ONWAI Corp")
    acct = _seed_acct()

    preview = svc.preview_8b_import(_SAMPLE_PDF, instrument_id=inst["id"])
    svc.commit_8b_import(_inject_account(preview, acct["id"]))

    ev = VestEventStore().all()[0]
    assert ev["date"] == "2024-03-01"
    assert TransactionStore().all() == []


def test_commit_idempotency(isolated, monkeypatch):
    """Re-committing the same payload must be a no-op."""
    _stub_llm(monkeypatch, _GOOD_ROWS[:2])
    inst = _seed_inst("ONWAI", "ONWAI Corp")
    acct = _seed_acct()

    preview = svc.preview_8b_import(_SAMPLE_PDF, instrument_id=inst["id"])
    payload = _inject_account(preview, acct["id"])

    first = svc.commit_8b_import(payload)
    assert first["vests_applied"] == 2
    assert first["vests_idempotent"] == 0

    second = svc.commit_8b_import(payload)
    assert second["vests_applied"] == 0
    assert second["vests_idempotent"] == 2

    # DB state unchanged from first commit.
    assert len(VestEventStore().all()) == 2
    assert TransactionStore().all() == []


def test_commit_vest_against_existing_grant(isolated, monkeypatch):
    """When an existing grant matches, no new grant is created and the vest goes against it."""
    _stub_llm(monkeypatch, _GOOD_ROWS[:2])
    inst = _seed_inst("ONWAI", "ONWAI Corp")
    acct = _seed_acct()

    equity_svc.apply_equity_change(
        {
            "type": "grant_create",
            "instrument_id": inst["id"],
            "account_id": acct["id"],
            "grant_date": "2023-03-01",
            "total_units": 600.0,
            "tax_event": "at_vest",
            "tranches": [
                {"vest_date": "2024-03-01", "units": 300.0},
                {"vest_date": "2025-03-01", "units": 300.0},
            ],
        }
    )
    assert len(GrantStore().all()) == 1

    preview = svc.preview_8b_import(_SAMPLE_PDF, instrument_id=inst["id"])
    assert preview["proposed_grants"] == []

    result = svc.commit_8b_import(preview)
    assert result["grants_created"] == 0  # no new grant
    assert result["vests_applied"] == 2

    # Still only one grant.
    assert len(GrantStore().all()) == 1
    assert len(VestEventStore().all()) == 2


def test_commit_malformed_payload_raises(isolated):
    with pytest.raises(svc.EightBImportError, match="dict"):
        svc.commit_8b_import("not a dict")


# ── API route tests ────────────────────────────────────────────────────────────────


def test_api_preview_returns_200(client, isolated, monkeypatch):
    _stub_llm(monkeypatch, _GOOD_ROWS[:1])
    _seed_inst("ONWAI", "ONWAI Corp")

    resp = client.post(
        "/api/equity/import/8b/preview",
        files={"file": ("appendix8b.pdf", _SAMPLE_PDF, "application/pdf")},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert "proposed_grants" in data
    assert "proposed_vests" in data
    assert "rows_extracted" in data


def test_api_preview_shape(client, isolated, monkeypatch):
    _stub_llm(monkeypatch, _GOOD_ROWS[:2])
    _seed_inst("ONWAI", "ONWAI Corp")

    resp = client.post(
        "/api/equity/import/8b/preview",
        files={"file": ("appendix8b.pdf", _SAMPLE_PDF, "application/pdf")},
    )
    data = resp.json()
    assert data["rows_extracted"] == 2
    assert len(data["proposed_grants"]) == 1
    grant = data["proposed_grants"][0]
    assert grant["grant_date"] == "2023-03-01"
    assert grant["total_units"] == pytest.approx(600.0)
    assert "proposed_tranches" in grant

    vests = data["proposed_vests"]
    assert len(vests) == 2
    for v in vests:
        assert "idempotency_key" in v
        assert v["idempotency_key"].startswith("8b:")
        assert "fmv_per_unit" in v
        assert "vest_date" in v


def test_api_commit_returns_200(client, isolated, monkeypatch):
    _stub_llm(monkeypatch, _GOOD_ROWS[:1])
    inst = _seed_inst("ONWAI", "ONWAI Corp")
    acct = _seed_acct()

    prev_resp = client.post(
        "/api/equity/import/8b/preview",
        files={"file": ("appendix8b.pdf", _SAMPLE_PDF, "application/pdf")},
        data={"instrument_id": inst["id"]},
    )
    assert prev_resp.status_code == 200
    payload = _inject_account(prev_resp.json(), acct["id"])

    commit_resp = client.post(
        "/api/equity/import/8b/commit",
        json=payload,
    )
    assert commit_resp.status_code == 200
    data = commit_resp.json()
    assert data["ok"] is True
    assert data["grants_created"] == 1
    assert data["vests_applied"] == 1


def test_api_commit_idempotent(client, isolated, monkeypatch):
    _stub_llm(monkeypatch, _GOOD_ROWS[:1])
    inst = _seed_inst("ONWAI", "ONWAI Corp")
    acct = _seed_acct()

    prev_resp = client.post(
        "/api/equity/import/8b/preview",
        files={"file": ("appendix8b.pdf", _SAMPLE_PDF, "application/pdf")},
        data={"instrument_id": inst["id"]},
    )
    payload = _inject_account(prev_resp.json(), acct["id"])

    first = client.post("/api/equity/import/8b/commit", json=payload)
    assert first.json()["vests_applied"] == 1

    second = client.post("/api/equity/import/8b/commit", json=payload)
    data = second.json()
    assert data["vests_applied"] == 0
    assert data["vests_idempotent"] == 1


def test_api_csrf_guard_blocks_no_header(isolated, monkeypatch):
    """Without X-Requested-With or Bearer, the commit route returns 403."""
    _stub_llm(monkeypatch, [])
    app = FastAPI()
    app.include_router(router)
    bare_client = TestClient(app)  # no default headers

    resp = bare_client.post("/api/equity/import/8b/commit", json={})
    assert resp.status_code == 403


def test_api_preview_422_on_empty_upload(client):
    resp = client.post(
        "/api/equity/import/8b/preview",
        files={"file": ("empty.pdf", b"", "application/pdf")},
    )
    assert resp.status_code == 422


def test_api_preview_422_on_non_pdf(client):
    resp = client.post(
        "/api/equity/import/8b/preview",
        files={"file": ("data.csv", b"Date,Amount\n2024-01-01,100\n", "text/csv")},
    )
    assert resp.status_code == 422


# ── Commit path: over-units and non-finite values ─────────────────────────────────


def test_commit_over_units_becomes_per_row_error(isolated, monkeypatch):
    """When a vest row has units > tranche.units, commit must surface it as a per-row
    error (in the errors list), not a 500."""
    _stub_llm(monkeypatch, [_GOOD_ROWS[0]])  # 300 units per tranche
    inst = _seed_inst("ONWAI", "ONWAI Corp")
    acct = _seed_acct()

    preview = svc.preview_8b_import(_SAMPLE_PDF, instrument_id=inst["id"])
    assert preview["ok"] is True

    # Tamper the vest row to have 999 units (well over the 300-unit tranche).
    payload = _inject_account(preview, acct["id"])
    tampered_vests = [dict(v) for v in payload["proposed_vests"]]
    tampered_vests[0] = {**tampered_vests[0], "units": 999.0}
    payload["proposed_vests"] = tampered_vests

    result = svc.commit_8b_import(payload)
    assert result["ok"] is True
    # The over-units vest must appear as an error, not raise.
    assert len(result["errors"]) >= 1
    error_texts = [e["error"] for e in result["errors"]]
    assert any("more units" in txt or "tranche" in txt.lower() for txt in error_texts), (
        f"Expected over-units error message, got: {error_texts}"
    )


def test_commit_nan_fmv_becomes_per_row_error(isolated, monkeypatch):
    """A vest row with NaN fmv_per_unit must be rejected as a per-row error."""
    _stub_llm(monkeypatch, [_GOOD_ROWS[0]])
    inst = _seed_inst("ONWAI", "ONWAI Corp")
    acct = _seed_acct()

    preview = svc.preview_8b_import(_SAMPLE_PDF, instrument_id=inst["id"])

    payload = _inject_account(preview, acct["id"])
    tampered_vests = [dict(v) for v in payload["proposed_vests"]]
    tampered_vests[0] = {**tampered_vests[0], "fmv_per_unit": float("nan")}
    payload["proposed_vests"] = tampered_vests

    result = svc.commit_8b_import(payload)
    assert result["ok"] is True
    assert len(result["errors"]) >= 1
    error_texts = [e["error"] for e in result["errors"]]
    assert any(
        "finite" in txt or "non-numeric" in txt or "nan" in txt.lower() for txt in error_texts
    ), f"Expected finite/nan error, got: {error_texts}"


def test_commit_inf_price_paid_becomes_per_row_error(isolated, monkeypatch):
    """A vest row with inf price_paid_per_unit must be rejected as a per-row error."""
    _stub_llm(monkeypatch, [_GOOD_ROWS[0]])
    inst = _seed_inst("ONWAI", "ONWAI Corp")
    acct = _seed_acct()

    preview = svc.preview_8b_import(_SAMPLE_PDF, instrument_id=inst["id"])

    payload = _inject_account(preview, acct["id"])
    tampered_vests = [dict(v) for v in payload["proposed_vests"]]
    tampered_vests[0] = {**tampered_vests[0], "price_paid_per_unit": float("inf")}
    payload["proposed_vests"] = tampered_vests

    result = svc.commit_8b_import(payload)
    assert result["ok"] is True
    assert len(result["errors"]) >= 1
    error_texts = [e["error"] for e in result["errors"]]
    assert any(
        "finite" in txt or "non-numeric" in txt or "inf" in txt.lower() for txt in error_texts
    ), f"Expected finite/inf error, got: {error_texts}"


def test_api_preview_document_currency_override(client, isolated, monkeypatch):
    _stub_llm(monkeypatch, _GOOD_ROWS[:1])
    _seed_inst("ONWAI", "ONWAI Corp")

    resp = client.post(
        "/api/equity/import/8b/preview",
        files={"file": ("8b.pdf", _SAMPLE_PDF, "application/pdf")},
        data={"document_currency": "USD"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["document_currency"] == "USD"
    vest = data["proposed_vests"][0]
    assert vest["fmv_currency"] == "USD"
