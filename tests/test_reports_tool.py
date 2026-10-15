"""
AI-tool test for get_report (P4-RP1).

Drives the registered tool via execute_tool (mirrors tests/test_rebalance_tool.py): asserts the
tool is registered, returns the JSON-stringified report model, is a pure read
(proposed_change None), passes type/period through, and surfaces an error for an unknown type.

Run:
    .venv/bin/python3 -m pytest tests/test_reports_tool.py -q
"""

from __future__ import annotations

import asyncio
import json

from backend.ai.tools import TOOL_SCHEMAS, execute_tool


def call(tool, **inp):
    text, proposed = asyncio.run(execute_tool(tool, inp))
    return json.loads(text), proposed


def _canned(monkeypatch):
    from backend.services import reports as reports_service

    captured = {}

    def fake(report_type, period="1Y"):
        captured["type"] = report_type
        captured["period"] = period
        return {
            "type": report_type,
            "title": "T",
            "period": period,
            "base_currency": "USD",
            "data": {"net_worth": 100.0},
        }

    monkeypatch.setattr(reports_service, "build_report", fake)
    return captured


def test_tool_registered():
    names = {t["name"] for t in TOOL_SCHEMAS}
    assert "get_report" in names


def test_returns_model_no_proposed_change(monkeypatch):
    captured = _canned(monkeypatch)
    res, proposed = call("get_report", type="net_worth_statement", period="3M")
    assert proposed is None  # pure read — never writes
    assert captured == {"type": "net_worth_statement", "period": "3M"}
    assert res["base_currency"] == "USD"


def test_default_period(monkeypatch):
    captured = _canned(monkeypatch)
    call("get_report", type="spending_summary")
    assert captured["period"] == "1Y"


def test_new_report_types_pass_through(monkeypatch):
    captured = _canned(monkeypatch)
    for rt in ("cash_flow_statement", "budget_vs_actual"):
        res, proposed = call("get_report", type=rt, period="3M")
        assert proposed is None
        assert captured["type"] == rt
        assert res["base_currency"] == "USD"


def test_new_types_in_schema_enum():
    schema = next(t for t in TOOL_SCHEMAS if t["name"] == "get_report")
    enum = schema["input_schema"]["properties"]["type"]["enum"]
    assert "cash_flow_statement" in enum
    assert "budget_vs_actual" in enum


def test_unknown_type_surfaces_error(monkeypatch):
    from backend.services import reports as reports_service

    def boom(rt, period="1Y"):
        raise ValueError(f"unknown report type: {rt}")

    monkeypatch.setattr(reports_service, "build_report", boom)
    res, proposed = call("get_report", type="bogus")
    assert proposed is None
    assert "error" in res
    assert "available" in res
