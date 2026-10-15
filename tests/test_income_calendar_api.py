"""TestClient smoke for GET /api/income/calendar (ADR 0020) — status + shape, network-free.

Exercises the real router → service wiring with the dividend_calendar module's deps
monkeypatched (FX, holdings, instruments, frozen today)."""

from __future__ import annotations

import datetime as _dt
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.income import router
from backend.services import dividend_calendar as dcal


class _FixedToday:
    @staticmethod
    def today() -> _dt.date:
        return _dt.date(2026, 6, 21)

    @staticmethod
    def fromisoformat(s: str) -> _dt.date:
        return _dt.date.fromisoformat(s)


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(dcal, "base_currency", lambda: "SGD")
    monkeypatch.setattr(dcal.lots_service, "_load_rates", lambda: {"SGD": 1.0, "USD": 1.35})
    monkeypatch.setattr(dcal, "user_today", _FixedToday.today)
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def _seed(monkeypatch, holdings, insts):
    monkeypatch.setattr(dcal.positions_service, "hydrated", lambda *a, **k: holdings)
    monkeypatch.setattr(dcal, "instruments", SimpleNamespace(all=lambda: insts))


def test_calendar_ok_and_shape(client, monkeypatch):
    _seed(
        monkeypatch,
        [
            {
                "position_id": "p1",
                "instrument_id": "i1",
                "quantity": 100,
                "quote_currency": "USD",
                "price": 379.0,
                "quote_per_unit": 1.0,
                "symbol": "MSFT",
            }
        ],
        [
            {
                "id": "i1",
                "symbol": "MSFT",
                "name": "Microsoft",
                "quote_currency": "USD",
                "next_ex_date": "2026-08-20",
                "next_pay_date": "2026-09-10",
                "last_dividend_amount": 0.91,
                "dividend_frequency": "quarterly",
            }
        ],
    )
    r = client.get("/api/income/calendar?days=90")
    assert r.status_code == 200
    body = r.json()
    assert set(body) >= {
        "base_currency",
        "horizon_days",
        "payments",
        "count",
        "total_base",
        "next_payment",
    }
    assert body["count"] == 1
    row = body["payments"][0]
    assert row["status"] == "scheduled"
    assert row["symbol"] == "MSFT"
    assert {"ex_date", "pay_date", "per_share", "currency", "amount_base"} <= set(row)


def test_calendar_days_clamped(client, monkeypatch):
    _seed(monkeypatch, [], [])
    assert client.get("/api/income/calendar?days=10000").json()["horizon_days"] == 365


def test_calendar_empty(client, monkeypatch):
    _seed(monkeypatch, [], [])
    body = client.get("/api/income/calendar").json()
    assert body["count"] == 0
    assert body["next_payment"] is None
    assert body["total_base"] == 0.0
