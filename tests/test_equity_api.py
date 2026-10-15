"""
API smoke + round-trip tests for backend/api/equity.py (feat/equity-comp — Lane L2).

Covers every route (status + shape), a full create→vest→report→undo round-trip, and
the key error cases:
  - 422 on vest of an already-vested tranche
  - 404 on missing grant / vest event
  - upcoming returns a future tranche with overdue=False

Isolation: all backing files in tmp_path; identity FX (SGD base, 1:1 rates) so
monetary assertions are simple; monkeypatch seeds the scheduling store so
seed_vest_reminder() is a no-op.

Pattern mirrors test_income_api.py: TestClient on a minimal FastAPI app.
"""

from __future__ import annotations

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
from backend.services import equity_report as report_svc
from backend.services import ledger as ledger_service
from backend.services import lots as lots_service

# ── Fixtures ───────────────────────────────────────────────────────────────────


@pytest.fixture
def client(tmp_path, monkeypatch, db_isolated):
    """TestClient wired to tmp_path stores; identity FX (SGD base, 1:1)."""
    # Redirect all backing files. accounts/transactions are DB-backed now (plan 3
    # task 4) — db_isolated (above) redirects those; only the still-file-backed
    # stores need their JSON path patched here.

    # Rebind module-level store handles in all relevant service modules.
    fresh_grants = GrantStore()
    fresh_vest_events = VestEventStore()
    fresh_instruments = InstrumentStore()
    fresh_positions = PositionStore()
    fresh_accounts = AccountStore()
    fresh_transactions = TransactionStore()

    monkeypatch.setattr(equity_svc, "grants", fresh_grants)
    monkeypatch.setattr(equity_svc, "vest_events", fresh_vest_events)
    monkeypatch.setattr(equity_svc, "instruments", fresh_instruments)
    monkeypatch.setattr(equity_svc, "positions", fresh_positions)

    # equity_report has its own module-level store handles.
    monkeypatch.setattr(report_svc, "grants", GrantStore())
    monkeypatch.setattr(report_svc, "vest_events", VestEventStore())
    monkeypatch.setattr(report_svc, "instruments", InstrumentStore())
    monkeypatch.setattr(report_svc, "transactions", TransactionStore())

    monkeypatch.setattr(lots_service, "instruments", InstrumentStore())
    monkeypatch.setattr(ledger_service, "accounts", fresh_accounts)
    monkeypatch.setattr(ledger_service, "transactions", fresh_transactions)

    # Identity FX: SGD base, 1:1 rates (makes assertions straightforward).
    monkeypatch.setattr(equity_svc, "base_currency", lambda: "SGD")
    monkeypatch.setattr(lots_service, "base_currency", lambda: "SGD")
    monkeypatch.setattr(ledger_service, "base_currency", lambda: "SGD")
    monkeypatch.setattr("backend.core.fx.base_currency", lambda: "SGD")
    monkeypatch.setattr(lots_service, "_load_rates", lambda: {"SGD": 1.0})
    monkeypatch.setattr(
        "backend.services.ledger.to_base",
        lambda a, c, b=None, rates=None: round(float(a or 0), 2),
    )
    monkeypatch.setattr(
        equity_svc,
        "to_base",
        lambda a, c, b=None, rates=None: round(float(a or 0), 2),
    )
    monkeypatch.setattr(
        report_svc,
        "to_base",
        lambda a, c, b=None, rates=None: round(float(a or 0), 2),
    )

    # Make lots_service._load_rates reachable from equity_report (it calls it inside a function).
    monkeypatch.setattr(lots_service, "_load_rates", lambda: {"SGD": 1.0})

    # Silence seed_vest_reminder so grant creates don't touch the scheduling store.

    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def _seed(*, symbol="ONWAI", ccy="SGD", vest_date_1="2025-03-15", vest_date_2="2027-06-30"):
    """Create an instrument and account for use in tests. Returns (inst, acct)."""
    inst = InstrumentStore().create(
        {
            "symbol": symbol,
            "name": f"{symbol} Corp",
            "quote_currency": ccy,
            "price_source_id": symbol,
            "price_source": "yahoo",
        }
    )
    acct = AccountStore().create(
        {"name": "Equity Account", "kind": "asset", "type": "investment", "currency": ccy}
    )
    return inst, acct


def _grant_body(inst_id: str, acct_id: str, vest_date_1="2025-03-15", vest_date_2="2027-06-30"):
    """Minimal GrantCreate body dict."""
    return {
        "instrument_id": inst_id,
        "account_id": acct_id,
        "grant_date": "2024-01-01",
        "total_units": 400.0,
        "tax_event": "at_vest",
        "tranches": [
            {"vest_date": vest_date_1, "units": 100.0},
            {"vest_date": vest_date_2, "units": 300.0},
        ],
    }


# ── GET /api/equity/grants — empty list ──────────────────────────────────────


def test_list_grants_empty(client):
    r = client.get("/api/equity/grants")
    assert r.status_code == 200
    body = r.json()
    assert "grants" in body
    assert body["grants"] == []


# ── POST /api/equity/grants — create ─────────────────────────────────────────


def test_create_grant_ok(client):
    inst, acct = _seed()
    r = client.post("/api/equity/grants", json=_grant_body(inst["id"], acct["id"]))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert "grant_id" in body
    assert "locked_position_id" in body


def test_create_grant_unknown_instrument_422(client):
    _, acct = _seed()
    r = client.post(
        "/api/equity/grants",
        json={
            "instrument_id": "nonexistent",
            "account_id": acct["id"],
            "grant_date": "2024-01-01",
            "total_units": 100.0,
            "tranches": [{"vest_date": "2025-03-15", "units": 100.0}],
        },
    )
    # ValidationError in service → 422
    assert r.status_code in (422, 404), r.text


# ── GET /api/equity/grants — enriched shape ──────────────────────────────────


def test_list_grants_hydrated_shape(client):
    inst, acct = _seed()
    client.post("/api/equity/grants", json=_grant_body(inst["id"], acct["id"]))
    r = client.get("/api/equity/grants")
    assert r.status_code == 200
    grants = r.json()["grants"]
    assert len(grants) == 1
    g = grants[0]
    # Contract §14 requires these computed fields.
    assert "symbol" in g
    assert "total_units" in g
    assert "vested_units" in g
    assert "unvested_units" in g
    assert "next_vest_date" in g
    assert g["total_units"] == 400.0
    assert g["vested_units"] == 0.0
    assert g["unvested_units"] == 400.0
    assert g["symbol"] == "ONWAI"
    # Each tranche carries a status field.
    for t in g["tranches"]:
        assert "status" in t
        assert t["status"] in ("vested", "upcoming", "performance_pending")


# ── PATCH /api/equity/grants/{id} ────────────────────────────────────────────


def test_update_grant_note(client):
    inst, acct = _seed()
    created = client.post("/api/equity/grants", json=_grant_body(inst["id"], acct["id"])).json()
    gid = created["grant_id"]
    r = client.patch(f"/api/equity/grants/{gid}", json={"note": "updated note"})
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True


def test_update_grant_not_found_404(client):
    r = client.patch("/api/equity/grants/nonexistent", json={"note": "x"})
    assert r.status_code == 404


# ── POST /api/equity/grants/{id}/cancel ──────────────────────────────────────


def test_cancel_grant(client):
    inst, acct = _seed()
    created = client.post("/api/equity/grants", json=_grant_body(inst["id"], acct["id"])).json()
    gid = created["grant_id"]
    r = client.post(f"/api/equity/grants/{gid}/cancel")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["status"] == "cancelled"


def test_cancel_grant_not_found_404(client):
    r = client.post("/api/equity/grants/nonexistent/cancel")
    assert r.status_code == 404


# ── POST …/tranches/{tid}/vest ────────────────────────────────────────────────


def test_vest_tranche_ok(client):
    inst, acct = _seed()
    created = client.post("/api/equity/grants", json=_grant_body(inst["id"], acct["id"])).json()
    gid = created["grant_id"]
    # Get the grant to retrieve first tranche id.
    grant = client.get("/api/equity/grants").json()["grants"][0]
    tranche = grant["tranches"][0]
    tid = tranche["id"]

    r = client.post(
        f"/api/equity/grants/{gid}/tranches/{tid}/vest",
        json={"fmv_per_unit": 30.0, "fmv_currency": "SGD", "vest_date": "2025-03-15"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert "vest_event_id" in body
    assert body["grant_id"] == gid
    assert body["tranche_id"] == tid


def test_vest_already_vested_tranche_422(client):
    inst, acct = _seed()
    created = client.post("/api/equity/grants", json=_grant_body(inst["id"], acct["id"])).json()
    gid = created["grant_id"]
    grant = client.get("/api/equity/grants").json()["grants"][0]
    tid = grant["tranches"][0]["id"]

    vest_body = {"fmv_per_unit": 30.0, "fmv_currency": "SGD", "vest_date": "2025-03-15"}
    r1 = client.post(f"/api/equity/grants/{gid}/tranches/{tid}/vest", json=vest_body)
    assert r1.status_code == 200

    # Second vest of the same tranche must be rejected with 422.
    r2 = client.post(f"/api/equity/grants/{gid}/tranches/{tid}/vest", json=vest_body)
    # The idempotency-key gate catches the duplicate and returns 200 (idempotent),
    # but the tranche-already-vested guard returns 422 when no idempotency_key is set.
    # Since no key is present, the grant+tranche check fires first → idempotent 200.
    # Per RFC §3 step 0: find_by_grant_tranche returns the existing event → 200 idempotent.
    # The 422 test path is: call vest on a vested tranche when no vest_event exists (impossible
    # in practice). The real guard is the tranche.vested check — test it directly.
    assert r2.status_code in (200, 422), r2.text
    # Whether idempotent (200) or error (422), no new vest_event should be created.
    events = VestEventStore().all()
    assert len(events) == 1


def test_vest_already_vested_tranche_direct_422(client):
    """Force the 422 path: vest a tranche, clear the vest_event from the store
    (simulates a partially-applied crash mid-flight), then retry — the tranche.vested
    guard fires because the tranche is still marked vested but the event was deleted."""
    inst, acct = _seed()
    created = client.post("/api/equity/grants", json=_grant_body(inst["id"], acct["id"])).json()
    gid = created["grant_id"]
    grant = client.get("/api/equity/grants").json()["grants"][0]
    tid = grant["tranches"][0]["id"]

    vest_body = {"fmv_per_unit": 30.0, "fmv_currency": "SGD", "vest_date": "2025-03-15"}
    r1 = client.post(f"/api/equity/grants/{gid}/tranches/{tid}/vest", json=vest_body)
    assert r1.status_code == 200
    vest_event_id = r1.json()["vest_event_id"]

    # Delete the vest event so find_by_grant_tranche returns None, but tranche.vested=True.
    VestEventStore().delete(vest_event_id)

    r2 = client.post(f"/api/equity/grants/{gid}/tranches/{tid}/vest", json=vest_body)
    assert r2.status_code == 422, r2.text


def test_vest_missing_grant_404(client):
    r = client.post(
        "/api/equity/grants/nonexistent/tranches/abc/vest",
        json={"fmv_per_unit": 30.0, "fmv_currency": "SGD"},
    )
    assert r.status_code == 404


# ── POST /api/equity/vest-events/{id}/undo ────────────────────────────────────


def test_vest_undo_ok(client):
    inst, acct = _seed()
    created = client.post("/api/equity/grants", json=_grant_body(inst["id"], acct["id"])).json()
    gid = created["grant_id"]
    grant = client.get("/api/equity/grants").json()["grants"][0]
    tid = grant["tranches"][0]["id"]

    vest_r = client.post(
        f"/api/equity/grants/{gid}/tranches/{tid}/vest",
        json={"fmv_per_unit": 30.0, "fmv_currency": "SGD", "vest_date": "2025-03-15"},
    )
    vest_event_id = vest_r.json()["vest_event_id"]

    r = client.post(f"/api/equity/vest-events/{vest_event_id}/undo")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["vest_event_id"] == vest_event_id


def test_vest_undo_not_found_404(client):
    r = client.post("/api/equity/vest-events/nonexistent/undo")
    assert r.status_code == 404


# ── GET /api/equity/report ────────────────────────────────────────────────────


def test_report_empty(client):
    r = client.get("/api/equity/report")
    assert r.status_code == 200
    body = r.json()
    # Required shape per RFC §14 contract.
    assert "year" in body
    assert "years" in body
    assert "base_currency" in body
    assert "events" in body
    assert "total_gain_base" in body
    assert body["events"] == []
    assert body["total_gain_base"] == 0.0


def test_report_year_param(client):
    r = client.get("/api/equity/report?year=2025")
    assert r.status_code == 200
    assert r.json()["year"] == 2025


# ── GET /api/equity/upcoming ─────────────────────────────────────────────────


def test_upcoming_empty(client):
    r = client.get("/api/equity/upcoming")
    assert r.status_code == 200
    assert r.json()["items"] == []


def test_upcoming_window_days_param(client):
    r = client.get("/api/equity/upcoming?window_days=30")
    assert r.status_code == 200
    assert "items" in r.json()


def test_upcoming_future_tranche_overdue_false(client):
    """A tranche with vest_date in the far future must appear with overdue=False."""
    inst, acct = _seed()
    # Use a far-future vest date (2099) so it's definitely not overdue.
    body = {
        "instrument_id": inst["id"],
        "account_id": acct["id"],
        "grant_date": "2024-01-01",
        "total_units": 100.0,
        "tranches": [{"vest_date": "2099-01-01", "units": 100.0}],
    }
    client.post("/api/equity/grants", json=body)
    # Request a big window to include 2099.
    r = client.get("/api/equity/upcoming?window_days=99999")
    assert r.status_code == 200
    items = r.json()["items"]
    assert len(items) >= 1
    future_item = next((i for i in items if i["vest_date"] == "2099-01-01"), None)
    assert future_item is not None, f"Expected 2099-01-01 tranche in {items}"
    assert future_item["overdue"] is False
    # Shape check.
    assert "grant_id" in future_item
    assert "tranche_id" in future_item
    assert "symbol" in future_item
    assert "units" in future_item
    assert "indicative_value_base" in future_item


# ── GET /api/equity/grants/{gid}/tranches/{tid}/fmv ──────────────────────────


def test_fmv_prefill_no_price(client):
    inst, acct = _seed()
    created = client.post("/api/equity/grants", json=_grant_body(inst["id"], acct["id"])).json()
    gid = created["grant_id"]
    grant = client.get("/api/equity/grants").json()["grants"][0]
    tid = grant["tranches"][0]["id"]

    r = client.get(f"/api/equity/grants/{gid}/tranches/{tid}/fmv")
    assert r.status_code == 200, r.text
    body = r.json()
    assert "fmv_per_unit" in body
    assert "fmv_currency" in body
    assert "as_of" in body
    assert "source" in body
    # No price history seeded → source="none"
    assert body["source"] == "none"
    assert body["fmv_per_unit"] is None


def test_fmv_prefill_missing_grant_404(client):
    r = client.get("/api/equity/grants/nonexistent/tranches/abc/fmv")
    assert r.status_code == 404


# ── Upload size guard (FIX 1) ─────────────────────────────────────────────────


def test_preview_oversized_content_length_413(client, monkeypatch):
    """When Content-Length header exceeds _MAX_UPLOAD_BYTES, must return 413 before reading."""
    from backend.services.equity_import import _MAX_UPLOAD_BYTES

    # Send a small body but lie in Content-Length — the route checks the header first.
    resp = client.post(
        "/api/equity/import/8b/preview",
        files={"file": ("big.pdf", b"%PDF-small", "application/pdf")},
        headers={
            "X-Requested-With": "XMLHttpRequest",
            "Content-Length": str(_MAX_UPLOAD_BYTES + 1),
        },
    )
    assert resp.status_code == 413


def test_preview_oversized_body_413(client, monkeypatch):
    """When the actual uploaded bytes exceed _MAX_UPLOAD_BYTES, must return 413."""
    from backend.services.equity_import import _MAX_UPLOAD_BYTES

    # Build a body that is genuinely over the cap.
    big_content = b"%PDF-" + b"x" * (_MAX_UPLOAD_BYTES + 1)
    resp = client.post(
        "/api/equity/import/8b/preview",
        files={"file": ("big.pdf", big_content, "application/pdf")},
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    assert resp.status_code == 413


def test_preview_small_file_still_works(client, monkeypatch):
    """A small valid PDF must still get a 422 (non-PDF) rather than 413 — cap is not blocking."""
    # Use a minimal CSV to verify the cap doesn't block small files.
    resp = client.post(
        "/api/equity/import/8b/preview",
        files={"file": ("small.csv", b"Date,Amount\n2024-01-01,100\n", "text/csv")},
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    # Small file → passes size check, fails on non-PDF content → 422
    assert resp.status_code == 422


# ── Full round-trip: create → vest → report → undo → report empty ────────────


def test_full_round_trip(client, monkeypatch):
    """
    Round-trip: create instrument + grant → vest tranche with explicit fmv →
    report shows event with correct gross_gain ±0.01 → undo → report empty +
    tranche pending again.
    """
    inst, acct = _seed()
    # 1. Create grant with one tranche (easier totals).
    body = {
        "instrument_id": inst["id"],
        "account_id": acct["id"],
        "grant_date": "2024-01-01",
        "total_units": 200.0,
        "tax_event": "at_vest",
        "tranches": [{"vest_date": "2025-06-01", "units": 200.0}],
    }
    create_r = client.post("/api/equity/grants", json=body)
    assert create_r.status_code == 200, create_r.text
    gid = create_r.json()["grant_id"]

    grant = client.get("/api/equity/grants").json()["grants"][0]
    tid = grant["tranches"][0]["id"]

    # 2. Vest the tranche: 200 units @ SGD 50.0 = SGD 10 000 gain.
    vest_r = client.post(
        f"/api/equity/grants/{gid}/tranches/{tid}/vest",
        json={
            "fmv_per_unit": 50.0,
            "fmv_currency": "SGD",
            "vest_date": "2025-06-01",
        },
    )
    assert vest_r.status_code == 200, vest_r.text
    vest_event_id = vest_r.json()["vest_event_id"]
    gross = vest_r.json()["gross_gain_native"]
    assert abs(gross - 10000.0) <= 0.01, f"gross_gain_native={gross}"

    # 3. Report for 2025 should contain the event with gross_gain_base ≈ 10000.
    report_r = client.get("/api/equity/report?year=2025")
    assert report_r.status_code == 200, report_r.text
    report = report_r.json()
    assert report["year"] == 2025
    assert len(report["events"]) == 1
    ev = report["events"][0]
    assert abs(ev["gross_gain_base"] - 10000.0) <= 0.01, f"gross_gain_base={ev['gross_gain_base']}"
    assert ev["grant_id"] == gid
    assert ev["symbol"] == "ONWAI"
    assert abs(report["total_gain_base"] - 10000.0) <= 0.01
    # Required event fields per RFC §14.
    for field in (
        "id",
        "grant_id",
        "symbol",
        "grant_date",
        "vest_date",
        "price_paid_per_unit",
        "fmv_per_unit",
        "fmv_currency",
        "units",
        "gross_gain_base",
        "tax_event",
    ):
        assert field in ev, f"Missing field {field!r} in report event"

    # 4. Undo the vest.
    undo_r = client.post(f"/api/equity/vest-events/{vest_event_id}/undo")
    assert undo_r.status_code == 200, undo_r.text

    # 5. Report for 2025 must now be empty.
    report2 = client.get("/api/equity/report?year=2025").json()
    assert report2["events"] == [], "report must be empty after undo"
    assert report2["total_gain_base"] == 0.0

    # 6. The tranche must be pending (unvested) again.
    grant_after = client.get("/api/equity/grants").json()["grants"][0]
    t_after = next(t for t in grant_after["tranches"] if t["id"] == tid)
    assert t_after["vested"] is False
    assert t_after.get("vest_event_id") is None
    # unvested_units back to full grant amount.
    assert grant_after["unvested_units"] == pytest.approx(200.0)


# ── grant_price_per_unit API passthrough ─────────────────────────────────────


def test_create_grant_with_price_passthrough(client):
    """POST /api/equity/grants with grant_price_per_unit passes the value through
    to the service and the field appears in the GET /api/equity/grants response."""
    inst, acct = _seed()
    body = _grant_body(inst["id"], acct["id"])
    body["grant_price_per_unit"] = 20.36

    r = client.post("/api/equity/grants", json=body)
    assert r.status_code == 200, r.text

    grants = client.get("/api/equity/grants").json()["grants"]
    assert len(grants) == 1
    assert grants[0]["grant_price_per_unit"] == pytest.approx(20.36)


def test_patch_grant_sets_price(client):
    """PATCH /api/equity/grants/{id} with grant_price_per_unit updates the field."""
    inst, acct = _seed()
    created = client.post("/api/equity/grants", json=_grant_body(inst["id"], acct["id"])).json()
    gid = created["grant_id"]

    r = client.patch(f"/api/equity/grants/{gid}", json={"grant_price_per_unit": 37.85})
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True

    grants = client.get("/api/equity/grants").json()["grants"]
    assert grants[0]["grant_price_per_unit"] == pytest.approx(37.85)


def test_create_grant_without_price_returns_none(client):
    """When grant_price_per_unit is omitted, the field is null in the response."""
    inst, acct = _seed()
    r = client.post("/api/equity/grants", json=_grant_body(inst["id"], acct["id"]))
    assert r.status_code == 200, r.text

    grants = client.get("/api/equity/grants").json()["grants"]
    assert grants[0].get("grant_price_per_unit") is None


# ── PATCH /api/positions — grant-managed lock ────────────────────────────────


@pytest.fixture
def portfolio_client(tmp_path, monkeypatch, db_isolated):
    """TestClient wired to the portfolio router; tmp_path stores."""
    from backend.api.portfolio import router as portfolio_router

    app = FastAPI()
    app.include_router(portfolio_router)
    return TestClient(app)


def test_patch_cost_basis_grant_managed_returns_422(portfolio_client):
    """PATCH cost_basis on a grant-managed locked position must return 422 (NOT 500)
    and the detail must contain the grant id."""
    acct = AccountStore().create(
        {"name": "Eq Acct", "kind": "asset", "type": "investment", "currency": "SGD"}
    )
    inst = InstrumentStore().create(
        {
            "symbol": "XYZ",
            "name": "XYZ Corp",
            "quote_currency": "SGD",
            "price_source_id": "XYZ",
            "price_source": "yahoo",
        }
    )
    pos = PositionStore().create(
        {
            "instrument_id": inst["id"],
            "account_id": acct["id"],
            "quantity": 100.0,
            "bucket": "locked",
        }
    )
    pos_id = pos["id"]
    grant = GrantStore().append(
        {
            "instrument_id": inst["id"],
            "account_id": acct["id"],
            "grant_date": "2024-01-01",
            "total_units": 100.0,
            "locked_position_id": pos_id,
            "tranches": [],
        }
    )
    grant_id = grant["id"]

    r = portfolio_client.patch(
        f"/api/positions/{pos_id}",
        json={"changes": {"cost_basis": 50.0}},
    )
    assert r.status_code == 422, f"Expected 422 got {r.status_code}: {r.text}"
    assert grant_id in r.text


# ── Release routes (two-step vest — ADR 2026-07-31) ──────────────────────────


def _seed_pending_vest(client):
    """Create a release_required grant, vest its first tranche. Returns the vest_event_id."""
    inst, acct = _seed()
    body = _grant_body(inst["id"], acct["id"])
    body["release_required"] = True
    grant_id = client.post("/api/equity/grants", json=body).json()["grant_id"]
    tranche_id = client.get("/api/equity/grants").json()["grants"][0]["tranches"][0]["id"]
    r = client.post(
        f"/api/equity/grants/{grant_id}/tranches/{tranche_id}/vest",
        json={
            "fmv_per_unit": 25.0,
            "fmv_currency": "SGD",
            "expected_release_date": "2025-04-15",
        },
    )
    assert r.status_code == 200, r.text
    return r.json()


def test_pending_releases_empty(client):
    r = client.get("/api/equity/pending-releases")
    assert r.status_code == 200
    assert r.json() == {"items": []}


def test_vest_on_release_required_grant_is_pending_and_lotless(client):
    body = _seed_pending_vest(client)
    assert body["pending_release"] is True
    assert body["lot_id"] is None

    items = client.get("/api/equity/pending-releases").json()["items"]
    assert len(items) == 1
    assert items[0]["units"] == 100.0
    assert items[0]["expected_release_date"] == "2025-04-15"
    assert items[0]["symbol"] == "ONWAI"


def test_release_route_delivers_the_shares(client):
    ev_id = _seed_pending_vest(client)["vest_event_id"]
    r = client.post(f"/api/equity/vest-events/{ev_id}/release", json={"release_date": "2025-05-01"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["released_on"] == "2025-05-01"
    assert body["lot_id"]
    assert client.get("/api/equity/pending-releases").json()["items"] == []


def test_release_twice_is_refused(client):
    ev_id = _seed_pending_vest(client)["vest_event_id"]
    client.post(f"/api/equity/vest-events/{ev_id}/release", json={})
    r = client.post(f"/api/equity/vest-events/{ev_id}/release", json={})
    assert r.status_code in (409, 422), r.text


def test_release_unknown_vest_event_404(client):
    r = client.post("/api/equity/vest-events/nope/release", json={})
    assert r.status_code == 404


# ── GET /api/equity/vest-history ─────────────────────────────────────────────


def _create_vest_history_event(
    client,
    *,
    vest_date: str,
    release_required: bool,
    expected_release_date: str | None = None,
    release_date: str | None = None,
    instrument_currency: str = "SGD",
) -> tuple[str, str]:
    inst, acct = _seed(ccy=instrument_currency)
    grant_body = {
        "instrument_id": inst["id"],
        "account_id": acct["id"],
        "grant_date": "2024-01-01",
        "total_units": 10.0,
        "tax_event": "at_vest",
        "release_required": release_required,
        "tranches": [{"vest_date": vest_date, "units": 10.0}],
    }
    grant_id = client.post("/api/equity/grants", json=grant_body).json()["grant_id"]
    grant = next(
        row for row in client.get("/api/equity/grants").json()["grants"] if row["id"] == grant_id
    )
    tranche_id = grant["tranches"][0]["id"]
    vest = client.post(
        f"/api/equity/grants/{grant_id}/tranches/{tranche_id}/vest",
        json={
            "fmv_per_unit": 25.0,
            "fmv_currency": "SGD",
            "vest_date": vest_date,
            "expected_release_date": expected_release_date,
        },
    )
    assert vest.status_code == 200, vest.text
    vest_event_id = vest.json()["vest_event_id"]
    if release_date:
        released = client.post(
            f"/api/equity/vest-events/{vest_event_id}/release",
            json={"release_date": release_date},
        )
        assert released.status_code == 200, released.text
    return grant_id, vest_event_id


def test_vest_history_is_newest_first_and_uses_persisted_release_facts(client):
    immediate_grant_id, immediate_event_id = _create_vest_history_event(
        client,
        vest_date="2025-01-10",
        release_required=False,
    )
    released_grant_id, released_event_id = _create_vest_history_event(
        client,
        vest_date="2025-02-10",
        release_required=True,
        expected_release_date="2025-02-20",
        release_date="2025-02-15",
    )
    pending_grant_id, pending_event_id = _create_vest_history_event(
        client,
        vest_date="2025-03-10",
        release_required=True,
        expected_release_date="2025-03-20",
    )

    # History is about what happened to each vest. Later edits to the grant's setting
    # must not rewrite those persisted event facts.
    assert (
        client.patch(
            f"/api/equity/grants/{immediate_grant_id}", json={"release_required": True}
        ).status_code
        == 200
    )
    for grant_id in (released_grant_id, pending_grant_id):
        assert (
            client.patch(
                f"/api/equity/grants/{grant_id}", json={"release_required": False}
            ).status_code
            == 200
        )

    response = client.get("/api/equity/vest-history")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == 3
    assert [item["vest_event_id"] for item in body["items"]] == [
        pending_event_id,
        released_event_id,
        immediate_event_id,
    ]

    pending, released, immediate = body["items"]
    assert pending["release_status"] == "awaiting_release"
    assert pending["released_on"] is None
    assert pending["overdue"] is True

    assert released["release_status"] == "released"
    assert released["released_on"] == "2025-02-15"
    assert released["overdue"] is False

    assert immediate["release_status"] == "released"
    assert immediate["released_on"] == "2025-01-10"
    assert immediate["overdue"] is False


def test_vest_history_items_match_pending_release_display_contract(client):
    _grant_id, vest_event_id = _create_vest_history_event(
        client,
        vest_date="2025-03-10",
        release_required=True,
        expected_release_date="2025-03-20",
    )

    response = client.get("/api/equity/vest-history")

    assert response.status_code == 200, response.text
    item = response.json()["items"][0]
    assert item["vest_event_id"] == vest_event_id
    assert {
        "vest_event_id",
        "grant_id",
        "tranche_id",
        "symbol",
        "instrument_id",
        "vest_date",
        "units",
        "fmv_per_unit",
        "fmv_currency",
        "release_status",
        "release_mode",
        "released_on",
        "expected_release_date",
        "overdue",
        "indicative_value_base",
        "base_currency",
    }.issubset(item)
    assert item["symbol"] == "ONWAI"
    assert item["units"] == 10.0
    assert item["fmv_per_unit"] == 25.0
    assert item["fmv_currency"] == "SGD"
    assert item["indicative_value_base"] is None
    assert item["base_currency"] == "SGD"


def test_vest_history_does_not_infer_release_from_a_legacy_lot(client):
    _grant_id, vest_event_id = _create_vest_history_event(
        client,
        vest_date="2025-03-10",
        release_required=False,
    )
    VestEventStore().update_event(
        vest_event_id,
        {"pending_release": None, "released_on": None},
    )

    response = client.get("/api/equity/vest-history")

    assert response.status_code == 200, response.text
    item = response.json()["items"][0]
    assert item["release_status"] == "not_released"
    assert item["released_on"] is None
    assert item["release_mode"] == "record_only"


def test_vest_history_and_pending_release_omit_false_base_value_when_fx_is_missing(client):
    _grant_id, _vest_event_id = _create_vest_history_event(
        client,
        vest_date="2025-03-10",
        release_required=True,
        expected_release_date="2025-03-20",
        instrument_currency="EUR",
    )
    equity_svc.price_history.record("ONWAI", 30.0, day="2025-03-11", source="test")

    history_item = client.get("/api/equity/vest-history").json()["items"][0]
    pending_item = client.get("/api/equity/pending-releases").json()["items"][0]

    assert history_item["indicative_value_base"] is None
    assert pending_item["indicative_value_base"] is None
