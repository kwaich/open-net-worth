"""
AI-tool test for get_forecast_scorecard (P3-SI1).

Drives the registered tool via execute_tool (mirrors tests/test_returns_tool.py): asserts the
JSON-stringified payload shape, that proposed_change is None (a pure read), and that an unknown
window coerces to the default rather than erroring. The service is monkeypatched to a canned
payload so no live data / network is touched.

Run:
    .venv/bin/python3 -m pytest tests/test_forecast_scorecard_tool.py -q
"""

from __future__ import annotations

import asyncio
import json

from backend.ai.tools import TOOL_SCHEMAS, execute_tool


def call(tool, **inp):
    text, proposed = asyncio.run(execute_tool(tool, inp))
    return json.loads(text), proposed


def _canned(monkeypatch):
    from backend.services import forecast_scorecard as scorecard_service

    captured = {}

    def fake(window="3M", today=None):
        captured["window"] = window
        return {
            "window": window,
            "base_currency": "USD",
            "forecastable": True,
            "projected_monthly_net": 2000.0,
            "periods": [],
            "cashflow_accuracy": {"mean_abs_error": 100.0, "bias": -20.0, "periods_scored": 3},
            "net_worth_accuracy": {"mean_abs_error": 50.0, "bias": 10.0, "periods_scored": 3},
            "data_quality": {"has_baseline": True},
        }

    monkeypatch.setattr(scorecard_service, "scorecard", fake)
    return captured


def test_tool_in_registered_set():
    names = {t["name"] for t in TOOL_SCHEMAS}
    assert "get_forecast_scorecard" in names


def test_tool_shape_and_no_proposed_change(monkeypatch):
    captured = _canned(monkeypatch)
    res, proposed = call("get_forecast_scorecard", window="6M")
    assert proposed is None  # pure read
    assert captured["window"] == "6M"
    assert {
        "window",
        "base_currency",
        "forecastable",
        "cashflow_accuracy",
        "net_worth_accuracy",
        "data_quality",
    } <= set(res)
    assert res["cashflow_accuracy"]["bias"] == -20.0


def test_tool_defaults_window_when_missing(monkeypatch):
    captured = _canned(monkeypatch)
    res, proposed = call("get_forecast_scorecard")
    assert proposed is None
    assert captured["window"] == "3M"


def test_tool_unknown_window_coerces_to_default(monkeypatch):
    captured = _canned(monkeypatch)
    call("get_forecast_scorecard", window="bogus")
    assert captured["window"] == "3M"
