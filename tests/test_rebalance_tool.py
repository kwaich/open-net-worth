"""
AI-tool test for get_rebalance_plan (P4-RB1).

Drives the registered tool via execute_tool (mirrors tests/test_returns_tool.py): asserts the
tool is registered, returns the JSON-stringified plan, is a pure read (proposed_change None),
and passes new_cash/mode through to the service.

Run:
    .venv/bin/python3 -m pytest tests/test_rebalance_tool.py -q
"""

from __future__ import annotations

import asyncio
import json

from backend.ai.tools import TOOL_SCHEMAS, execute_tool


def call(tool, **inp):
    text, proposed = asyncio.run(execute_tool(tool, inp))
    return json.loads(text), proposed


def _canned(monkeypatch):
    from backend.services import rebalance as rebalance_service

    captured = {}

    def fake(*, new_cash=0.0, mode="sell_to_target", rows=None, base=None):
        captured["new_cash"] = new_cash
        captured["mode"] = mode
        return {
            "base_currency": "USD",
            "mode": mode,
            "status": "ok",
            "new_cash": new_cash,
            "current_total": 10000.0,
            "target_total": 10000.0 + new_cash,
            "allocations": [],
            "trades": [],
        }

    monkeypatch.setattr(rebalance_service, "rebalance_plan", fake)
    return captured


def test_tool_registered():
    names = {t["name"] for t in TOOL_SCHEMAS}
    assert "get_rebalance_plan" in names


def test_returns_shape_and_no_proposed_change(monkeypatch):
    captured = _canned(monkeypatch)
    res, proposed = call("get_rebalance_plan")
    assert proposed is None  # pure read / suggestion — never writes
    assert captured == {"new_cash": 0.0, "mode": "sell_to_target"}
    assert {"base_currency", "mode", "status", "allocations", "trades"} <= set(res)


def test_passes_new_cash_and_mode(monkeypatch):
    captured = _canned(monkeypatch)
    res, proposed = call("get_rebalance_plan", new_cash=5000, mode="cash_only")
    assert proposed is None
    assert captured == {"new_cash": 5000, "mode": "cash_only"}
    assert res["target_total"] == 15000.0
