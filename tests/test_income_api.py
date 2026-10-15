"""
API smoke tests for the income router (P2-INV2) — status + shape, offline + isolated.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.income import router
from backend.db.account_store import AccountStore
from backend.db.income_event_store import IncomeEventStore
from backend.db.instrument_store import InstrumentStore
from backend.db.position_store import PositionStore
from backend.db.transaction_store import TransactionStore
from backend.services import income as svc
from backend.services import labels
from backend.services import ledger as ledger_service
from backend.services import lots as lots_service


@pytest.fixture
def client(tmp_path, monkeypatch, db_isolated):
    # accounts/transactions are DB-backed now (plan 3 task 4); income_events too (plan 4a
    # task 3) — db_isolated covers all three.
    monkeypatch.setattr(svc, "events", IncomeEventStore())
    monkeypatch.setattr(svc, "instruments", InstrumentStore())
    monkeypatch.setattr(svc, "positions", PositionStore())
    monkeypatch.setattr(lots_service, "instruments", InstrumentStore())
    monkeypatch.setattr(ledger_service, "accounts", AccountStore())
    monkeypatch.setattr(ledger_service, "transactions", TransactionStore())
    monkeypatch.setattr(svc, "base_currency", lambda: "USD")
    monkeypatch.setattr(lots_service, "_load_rates", lambda: {"USD": 1.0})
    monkeypatch.setattr(lots_service, "base_currency", lambda: "USD")
    monkeypatch.setattr(ledger_service, "base_currency", lambda: "USD")
    monkeypatch.setattr(
        "backend.services.ledger.to_base", lambda a, c, b=None, rates=None: round(float(a or 0), 2)
    )
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def _seed():
    inst = InstrumentStore().create(
        {"symbol": "ABC", "name": "ABC", "quote_currency": "USD", "price_source_id": "ABC"}
    )
    acct = AccountStore().create(
        {"name": "Cash", "kind": "asset", "type": "cash", "currency": "USD"}
    )
    pos = PositionStore().create(
        {"instrument_id": inst["id"], "account_id": acct["id"], "quantity": 100, "cost_basis": 10}
    )
    return inst, acct, pos


def test_get_income_empty(client):
    r = client.get("/api/income")
    assert r.status_code == 200
    assert r.json()["total_base"] == 0.0


def test_get_forecast_empty(client):
    r = client.get("/api/income/forecast?horizon_months=12")
    assert r.status_code == 200
    body = r.json()
    assert body["annual_total_base"] == 0.0
    assert body["horizon_months"] == 12


def test_post_cash_dividend_then_get(client):
    inst, acct, pos = _seed()
    r = client.post(
        "/api/income",
        json={
            "position_id": pos["id"],
            "gross_amount": 100.0,
            "tax_withheld": 10.0,
            "currency": "USD",
            "account_id": acct["id"],
            "paid_at": "2026-05-01",
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True
    summary = client.get("/api/income").json()
    assert summary["total_base"] == 90.0


def test_get_income_labels_resolvable_events_without_false_empty_labels(client):
    inst, acct, pos = _seed()
    IncomeEventStore().append(
        {
            "position_id": pos["id"],
            "instrument_id": inst["id"],
            "account_id": acct["id"],
            "paid_at": "2026-05-01",
            "income_kind": "dividend",
            "gross_amount": 100.0,
            "tax_withheld": 10.0,
            "currency": "USD",
            "base_amount": 999999.0,
            "base_currency": "SGD",
        }
    )
    IncomeEventStore().append(
        {
            "position_id": "missing-position",
            "instrument_id": "missing-instrument",
            "account_id": "missing-account",
            "paid_at": "2026-05-02",
            "income_kind": "interest",
            "gross_amount": 50.0,
            "tax_withheld": 5.0,
            "currency": "USD",
            "base_amount": 888888.0,
            "base_currency": "SGD",
        }
    )

    response = client.get("/api/income")

    assert response.status_code == 200, response.text
    known, unresolved = response.json()["events"]
    assert known["instrument_label"] == "ABC"
    assert known["instrument_ticker"] == "ABC"
    assert known["account_name"] == "Cash"
    assert known["gross_amount"] == 100.0
    assert known["tax_withheld"] == 10.0
    assert known["base_amount"] == 90.0
    assert "instrument_label" not in unresolved
    assert "instrument_ticker" not in unresolved
    assert "account_name" not in unresolved
    assert unresolved["base_amount"] == 45.0


def test_get_income_builds_display_label_maps_once_per_response(client, monkeypatch):
    inst, acct, pos = _seed()
    for paid_at in ("2026-05-01", "2026-05-02"):
        IncomeEventStore().append(
            {
                "position_id": pos["id"],
                "instrument_id": inst["id"],
                "account_id": acct["id"],
                "paid_at": paid_at,
                "income_kind": "dividend",
                "gross_amount": 10.0,
                "tax_withheld": 0.0,
                "currency": "USD",
            }
        )
    real_label_maps = labels.label_maps
    calls = 0

    def counting_label_maps(**kwargs):
        nonlocal calls
        calls += 1
        return real_label_maps(**kwargs)

    monkeypatch.setattr(labels, "label_maps", counting_label_maps)

    response = client.get("/api/income")

    assert response.status_code == 200, response.text
    assert len(response.json()["events"]) == 2
    assert calls == 1


def test_get_income_marks_missing_fx_instead_of_presenting_passthrough_as_base(client, monkeypatch):
    """A missing cross-currency rate is refused by core.fx (no 1:1). The income read must
    degrade: no base figure for that event, left out of every total, the currency named —
    so a client never presents USD 10 as SGD 10."""
    inst, acct, pos = _seed()
    IncomeEventStore().append(
        {
            "position_id": pos["id"],
            "instrument_id": inst["id"],
            "account_id": acct["id"],
            "paid_at": "2026-05-03",
            "income_kind": "interest",
            "gross_amount": 11.0,
            "tax_withheld": 1.0,
            "currency": "USD",
        }
    )
    monkeypatch.setattr(svc, "base_currency", lambda: "SGD")
    monkeypatch.setattr(lots_service, "_load_rates", lambda: {"SGD": 1.0})

    event = client.get("/api/income").json()["events"][0]

    assert event["base_amount"] is None
    assert event["base_amount_available"] is False
    assert event["native_net_amount"] == 10.0
    assert event["native_currency"] == "USD"
    summary = client.get("/api/income").json()
    assert summary["base_totals_available"] is False
    assert summary["yield_on_cost_available"] is False
    assert summary["missing_fx_currencies"] == ["USD"]
    assert summary["total_base"] == 0.0
    assert summary["trailing_12m_base"] == 0.0
    assert summary["by_instrument"] == {}
    assert summary["by_kind"] == {}


def test_get_income_missing_fx_event_is_left_out_of_known_totals(client, monkeypatch):
    inst, acct, pos = _seed()
    for ccy, gross in (("SGD", 20.0), ("USD", 11.0)):
        IncomeEventStore().append(
            {
                "position_id": pos["id"],
                "instrument_id": inst["id"],
                "account_id": acct["id"],
                "paid_at": "2026-05-03",
                "income_kind": "dividend",
                "gross_amount": gross,
                "tax_withheld": 0.0,
                "currency": ccy,
            }
        )
    monkeypatch.setattr(svc, "base_currency", lambda: "SGD")
    monkeypatch.setattr(lots_service, "_load_rates", lambda: {"SGD": 1.0})

    summary = client.get("/api/income").json()

    by_ccy = {e["currency"]: e for e in summary["events"]}
    assert by_ccy["SGD"]["base_amount"] == 20.0
    assert by_ccy["USD"]["base_amount"] is None
    assert summary["total_base"] == 20.0  # the known part only, flagged below
    assert summary["gross_base"] == 20.0
    assert summary["base_totals_available"] is False
    assert summary["missing_fx_currencies"] == ["USD"]


def test_post_drp(client):
    inst, acct, pos = _seed()
    r = client.post(
        "/api/income",
        json={
            "position_id": pos["id"],
            "gross_amount": 50.0,
            "currency": "USD",
            "is_drp": True,
            "drp_quantity": 5.0,
            "drp_unit_cost": 10.0,
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["is_drp"] is True


def test_post_unknown_position_422(client):
    r = client.post("/api/income", json={"position_id": "ghost", "gross_amount": 10})
    assert r.status_code == 422


def test_delete_income(client):
    inst, acct, pos = _seed()
    created = client.post(
        "/api/income",
        json={
            "position_id": pos["id"],
            "gross_amount": 100.0,
            "currency": "USD",
            "account_id": acct["id"],
        },
    ).json()
    r = client.delete(f"/api/income/{created['event_id']}")
    assert r.status_code == 200
    assert client.get("/api/income").json()["total_base"] == 0.0
