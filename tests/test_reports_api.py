"""
API smoke tests for the report-library router (P4-RP1).

Offline + isolated: a minimal FastAPI app mounts only the reports router; the service is
monkeypatched to canned outputs so we assert the GET catalog + report contract (JSON vs HTML),
the period passthrough, the unknown-type 404, and the download CSRF guard (X-Requested-With /
Bearer) — without touching real holdings/FX.

Run:
    .venv/bin/python3 -m pytest tests/test_reports_api.py -q
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api import reports as reports_api
from backend.api.reports import router


@pytest.fixture
def client(monkeypatch):
    calls = {}

    def fake_build(report_type, period="1Y"):
        if report_type == "nope":
            raise ValueError("unknown report type: nope")
        calls["type"] = report_type
        calls["period"] = period
        return {
            "type": report_type,
            "title": "Test Report",
            "description": "desc",
            "uses_period": True,
            "period": period,
            "base_currency": "USD",
            "generated_at": "2026-06-06T00:00:00+00:00",
            "app_name": "Open Net Worth",
            "data": {"x": 1},
        }

    monkeypatch.setattr(reports_api.reports_service, "build_report", fake_build)
    monkeypatch.setattr(
        reports_api.reports_service,
        "render_html",
        lambda report: f"<!doctype html><title>{report['title']}</title>",
    )
    monkeypatch.setattr(
        reports_api.reports_service,
        "list_reports",
        lambda: [
            {"type": "net_worth_statement", "title": "NW", "description": "d", "uses_period": True}
        ],
    )
    app = FastAPI()
    app.include_router(router)
    c = TestClient(app)
    c._calls = calls  # type: ignore[attr-defined]
    return c


def test_list_catalog(client):
    r = client.get("/api/reports")
    assert r.status_code == 200
    body = r.json()
    assert "reports" in body
    assert body["reports"][0]["type"] == "net_worth_statement"


def test_get_report_html_default(client):
    r = client.get("/api/reports/net_worth_statement")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert "<!doctype html>" in r.text
    assert client._calls["period"] == "1Y"


def test_get_report_period_passthrough(client):
    r = client.get("/api/reports/realized_income?period=YTD")
    assert r.status_code == 200
    assert client._calls["period"] == "YTD"


def test_get_report_json_via_format(client):
    r = client.get("/api/reports/net_worth_statement?format=json")
    assert r.status_code == 200
    assert r.json()["type"] == "net_worth_statement"


def test_get_report_json_via_accept(client):
    r = client.get("/api/reports/net_worth_statement", headers={"accept": "application/json"})
    assert r.status_code == 200
    assert r.json()["base_currency"] == "USD"


def test_get_cash_flow_statement(client):
    r = client.get("/api/reports/cash_flow_statement?period=3M")
    assert r.status_code == 200
    assert "<!doctype html>" in r.text
    assert client._calls["type"] == "cash_flow_statement"
    assert client._calls["period"] == "3M"


def test_get_budget_vs_actual(client):
    r = client.get("/api/reports/budget_vs_actual?format=json")
    assert r.status_code == 200
    assert r.json()["type"] == "budget_vs_actual"


def test_unknown_type_404(client):
    r = client.get("/api/reports/nope")
    assert r.status_code == 404


def test_download_requires_csrf_header(client):
    # No Bearer + no X-Requested-With → 403 (mirrors the export route guard).
    r = client.get("/api/reports/net_worth_statement?download=1")
    assert r.status_code == 403


def test_download_with_csrf_header_ok(client):
    r = client.get(
        "/api/reports/net_worth_statement?download=1",
        headers={"X-Requested-With": "XMLHttpRequest"},
    )
    assert r.status_code == 200
    assert "attachment" in r.headers.get("content-disposition", "")


def test_download_with_bearer_ok(client):
    r = client.get(
        "/api/reports/net_worth_statement?download=1",
        headers={"Authorization": "Bearer sometoken"},
    )
    assert r.status_code == 200
