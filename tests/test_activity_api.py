"""
API smoke tests for the investment activity router (lane/fix-activity-contract).

Asserts the exact ActivityFeed contract:
  { base, events: [ActivityRow], summary: { count_30d, net_invested_30d, income_30d, fees_30d } }

Each ActivityRow has FLAT fields: source_id, source_kind, date, type, symbol, name, detail,
account_id, account_name, amount_base, signed, currency, instrument_id, links.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.activity import router
from backend.db.account_store import AccountStore
from backend.db.grant_store import GrantStore
from backend.db.income_event_store import IncomeEventStore
from backend.db.instrument_store import InstrumentStore
from backend.db.lot_store import LotStore
from backend.db.position_store import PositionStore
from backend.db.realized_event_store import RealizedEventStore
from backend.db.vest_event_store import VestEventStore
from backend.services import activity as svc


@pytest.fixture
def client(tmp_path, monkeypatch, db_isolated):
    # income_events is DB-backed now (plan 4a task 3) — db_isolated covers it.

    monkeypatch.setattr(svc, "_accounts", AccountStore())
    monkeypatch.setattr(svc, "_grants", GrantStore())
    monkeypatch.setattr(svc, "_income_events", IncomeEventStore())
    monkeypatch.setattr(svc, "_instruments", InstrumentStore())
    monkeypatch.setattr(svc, "_lots", LotStore())
    monkeypatch.setattr(svc, "_realized_events", RealizedEventStore())
    monkeypatch.setattr(svc, "_vest_events", VestEventStore())

    monkeypatch.setattr(svc, "base_currency", lambda: "USD")
    monkeypatch.setattr(svc, "to_base", lambda a, c, b=None, rates=None: float(a))
    monkeypatch.setattr(svc, "_load_rates", lambda: {"USD": 1.0})

    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def _seed_lot(account_id: str, position_id: str, instrument_id: str) -> dict:
    return LotStore().create(
        {
            "position_id": position_id,
            "account_id": account_id,
            "instrument_id": instrument_id,
            "kind": "buy",
            "original_qty": 10.0,
            "remaining_qty": 10.0,
            "unit_cost": 50.0,
            "cost_currency": "USD",
            "acquired_at": "2026-01-15",
            "fees": 5.0,
        }
    )


def _seed_income_event(account_id: str, position_id: str, instrument_id: str) -> dict:
    return IncomeEventStore().append(
        {
            "position_id": position_id,
            "account_id": account_id,
            "instrument_id": instrument_id,
            "income_kind": "dividend",
            "gross_amount": 20.0,
            "tax_withheld": 0.0,
            "currency": "USD",
            "paid_at": "2026-02-01",
            "quantity": 10.0,
            "amount_per_unit": 2.0,
        }
    )


# ── Shape contract tests ──────────────────────────────────────────────────────


def test_empty_feed_shape(client):
    r = client.get("/api/investments/activity")
    assert r.status_code == 200
    body = r.json()
    assert "events" in body
    assert "summary" in body
    assert "base" in body
    assert body["events"] == []
    assert body["base"] == "USD"


def test_summary_keys_present(client):
    r = client.get("/api/investments/activity")
    summary = r.json()["summary"]
    assert "count_30d" in summary
    assert "net_invested_30d" in summary
    assert "income_30d" in summary
    assert "fees_30d" in summary


def test_buy_row_flat_shape(client, tmp_path):
    acct = AccountStore().create(
        {"name": "Vantage Brokerage", "kind": "asset", "type": "investment", "currency": "USD"}
    )
    inst = InstrumentStore().create(
        {"symbol": "AAPL", "name": "Apple Inc", "quote_currency": "USD", "price_source_id": "AAPL"}
    )
    pos = PositionStore().create(
        {"instrument_id": inst["id"], "account_id": acct["id"], "quantity": 10}
    )

    _seed_lot(acct["id"], pos["id"], inst["id"])

    r = client.get("/api/investments/activity")
    body = r.json()
    events = body["events"]

    buy_rows = [e for e in events if e["type"] == "buy"]
    assert len(buy_rows) == 1
    row = buy_rows[0]

    # Exact flat fields required by ActivityRow
    assert row["source_id"].startswith("buy:")
    assert row["source_kind"] == "lot"
    assert row["date"] == "2026-01-15"
    assert row["type"] == "buy"
    assert row["symbol"] == "AAPL"
    assert row["name"] == "Apple Inc"
    assert row["detail"] is not None
    assert row["account_id"] == acct["id"]
    assert row["account_name"] == "Vantage Brokerage"
    assert isinstance(row["amount_base"], float)
    assert row["amount_base"] < 0  # buy is cash out
    assert row["signed"] is True

    # fee row also emitted
    fee_rows = [e for e in events if e["type"] == "fee"]
    assert len(fee_rows) == 1
    assert fee_rows[0]["amount_base"] < 0
    assert fee_rows[0]["signed"] is True


def test_dividend_row_flat_shape(client, tmp_path):
    acct = AccountStore().create(
        {"name": "Vantage Brokerage USD", "kind": "asset", "type": "investment", "currency": "USD"}
    )
    inst = InstrumentStore().create(
        {"symbol": "MSFT", "name": "Microsoft", "quote_currency": "USD", "price_source_id": "MSFT"}
    )
    pos = PositionStore().create(
        {"instrument_id": inst["id"], "account_id": acct["id"], "quantity": 10}
    )

    _seed_income_event(acct["id"], pos["id"], inst["id"])

    r = client.get("/api/investments/activity")
    body = r.json()
    div_rows = [e for e in body["events"] if e["type"] == "dividend"]
    assert len(div_rows) == 1
    row = div_rows[0]

    assert row["source_kind"] == "income_event"
    assert row["symbol"] == "MSFT"
    assert row["name"] == "Microsoft"
    assert row["amount_base"] == 20.0
    assert row["signed"] is True
    assert row["account_name"] == "Vantage Brokerage USD"


def test_vest_row_signed_false(client, tmp_path):
    acct = AccountStore().create(
        {"name": "MyBroker", "kind": "asset", "type": "investment", "currency": "USD"}
    )
    inst = InstrumentStore().create(
        {"symbol": "GOOG", "name": "Alphabet", "quote_currency": "USD", "price_source_id": "GOOG"}
    )

    VestEventStore().append(
        {
            "grant_id": "grant-1",
            "tranche_id": "t1",
            "date": "2026-03-01",
            "units": 5.0,
            "fmv_per_unit": 100.0,
            "fmv_currency": "USD",
            "price_paid_per_unit": 0.0,
            "units_withheld": 0.0,
            "gross_gain_native": 500.0,
            "txn_id": "txn-abc",
            "account_id": acct["id"],
            "instrument_id": inst["id"],
        }
    )

    r = client.get("/api/investments/activity")
    vest_rows = [e for e in r.json()["events"] if e["type"] == "vest"]
    assert len(vest_rows) == 1
    assert vest_rows[0]["signed"] is False


def test_type_filter(client, tmp_path):
    acct = AccountStore().create(
        {"name": "Vantage Brokerage", "kind": "asset", "type": "investment", "currency": "USD"}
    )
    inst = InstrumentStore().create(
        {"symbol": "VTI", "name": "Vanguard", "quote_currency": "USD", "price_source_id": "VTI"}
    )
    pos = PositionStore().create(
        {"instrument_id": inst["id"], "account_id": acct["id"], "quantity": 10}
    )
    _seed_lot(acct["id"], pos["id"], inst["id"])
    _seed_income_event(acct["id"], pos["id"], inst["id"])

    r = client.get("/api/investments/activity?type=dividend")
    events = r.json()["events"]
    assert all(e["type"] == "dividend" for e in events)

    r2 = client.get("/api/investments/activity?type=buy")
    events2 = r2.json()["events"]
    assert all(e["type"] == "buy" for e in events2)


def test_invalid_type_422(client):
    r = client.get("/api/investments/activity?type=unknown_type")
    assert r.status_code == 422


def test_date_filter(client, tmp_path):
    acct = AccountStore().create(
        {"name": "Vantage Brokerage", "kind": "asset", "type": "investment", "currency": "USD"}
    )
    inst = InstrumentStore().create(
        {"symbol": "SPY", "name": "S&P 500", "quote_currency": "USD", "price_source_id": "SPY"}
    )
    pos = PositionStore().create(
        {"instrument_id": inst["id"], "account_id": acct["id"], "quantity": 10}
    )
    _seed_lot(acct["id"], pos["id"], inst["id"])  # date 2026-01-15

    r = client.get("/api/investments/activity?date_from=2026-02-01")
    assert r.json()["events"] == []

    r2 = client.get("/api/investments/activity?date_to=2026-01-31")
    assert len(r2.json()["events"]) > 0


def test_30d_summary_counts(client, tmp_path):
    from datetime import date

    today = date.today().isoformat()
    acct = AccountStore().create(
        {"name": "Vantage Brokerage", "kind": "asset", "type": "investment", "currency": "USD"}
    )
    inst = InstrumentStore().create(
        {"symbol": "BND", "name": "Bond Fund", "quote_currency": "USD", "price_source_id": "BND"}
    )
    pos = PositionStore().create(
        {"instrument_id": inst["id"], "account_id": acct["id"], "quantity": 10}
    )

    # Lot with today's date so it falls in the 30-day window
    LotStore().create(
        {
            "position_id": pos["id"],
            "account_id": acct["id"],
            "instrument_id": inst["id"],
            "kind": "buy",
            "original_qty": 5.0,
            "remaining_qty": 5.0,
            "unit_cost": 100.0,
            "cost_currency": "USD",
            "acquired_at": today,
            "fees": 0.0,
        }
    )

    r = client.get("/api/investments/activity")
    summary = r.json()["summary"]
    assert summary["count_30d"] >= 1
    # "Net invested" follows its label: buys ADD to it (5 × 100 USD here), sells
    # subtract. (The pre-fix code summed cash-flow-signed rows so both buys AND
    # sells pushed it negative — it showed −(total volume). Sign-bug regression,
    # owner report 2026-07-12.)
    assert summary["net_invested_30d"] > 0


def test_transfer_row_through_the_disposal_choke(client, tmp_path, monkeypatch):
    """A custodian-to-custodian move — booked through the REAL disposal choke as a
    non-taxable $0 disposal naming its destination — is served as ONE `transfer` row:
    unsigned, at 0, detail "→ <destination>", never a $0 Sell. `type=transfer` filters it
    and the destination account's filter still finds it."""
    from backend.services import lots as lots_service

    monkeypatch.setattr(lots_service, "_load_rates", lambda: {"USD": 1.0, "EUR": 1.0})
    monkeypatch.setattr(lots_service, "base_currency", lambda: "USD")
    src = AccountStore().create(
        {"name": "Workplace Custodian", "kind": "asset", "type": "investment", "currency": "EUR"}
    )
    dst = AccountStore().create(
        {"name": "IBKR", "kind": "asset", "type": "investment", "currency": "USD"}
    )
    inst = InstrumentStore().create(
        {
            "symbol": "KSTL",
            "name": "Kestrel Dash",
            "quote_currency": "EUR",
            "price_source_id": "KSTL",
        }
    )
    pos = PositionStore().create(
        {"instrument_id": inst["id"], "account_id": src["id"], "quantity": 1242.0}
    )
    LotStore().create(
        {
            "position_id": pos["id"],
            "instrument_id": inst["id"],
            "account_id": src["id"],
            "acquired_at": "2024-03-01",
            "original_qty": 1242.0,
            "remaining_qty": 1242.0,
            "unit_cost": 37.75,
            "cost_currency": "EUR",
            "kind": "grant",
        }
    )
    out = lots_service.apply_lot_change(
        {
            "type": "dispose",
            "position_id": pos["id"],
            "quantity": 1242.0,
            "proceeds_per_unit": 0.0,
            "proceeds_currency": "EUR",
            "disposed_at": "2026-07-25",
            "is_taxable": False,
            "transfer_to_account_id": "IBKR",
            "note": "moved to the other broker",
        }
    )
    assert out["ok"], out

    r = client.get("/api/investments/activity?type=transfer")
    assert r.status_code == 200
    events = r.json()["events"]
    assert [e["type"] for e in events] == ["transfer"]
    row = events[0]
    assert row["symbol"] == "KSTL"
    assert row["detail"] == "1242 units → IBKR"
    assert row["amount_base"] == 0.0
    assert row["signed"] is False
    assert row["account_name"] == "Workplace Custodian"
    assert row["links"]["to_account_id"] == dst["id"]

    assert client.get("/api/investments/activity?type=sell").json()["events"] == []
    on_dest = client.get(f"/api/investments/activity?account_id={dst['id']}").json()["events"]
    assert [e["type"] for e in on_dest] == ["transfer"]
    assert client.get("/api/investments/activity?type=transfer_out").status_code == 422
