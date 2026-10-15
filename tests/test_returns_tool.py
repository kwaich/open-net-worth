"""
AI-tool test for get_returns (P2-INV3).

Drives the registered tool via execute_tool (mirrors tests/test_lots_tools.py): asserts the
JSON-stringified payload shape, that proposed_change is None (a pure read), and that an
unknown period coerces to the 1Y default rather than erroring. The service is monkeypatched
to a canned payload so no live data / network is touched.

Run:
    .venv/bin/python3 -m pytest tests/test_returns_tool.py -q
"""

from __future__ import annotations

import asyncio
import json

from backend.ai.tools import TOOL_SCHEMAS, execute_tool


def call(tool, **inp):
    text, proposed = asyncio.run(execute_tool(tool, inp))
    return json.loads(text), proposed


def _canned(monkeypatch):
    from backend.services import returns as returns_service

    captured = {}

    def fake(period="1Y", benchmark=None, today=None, **_range):
        captured["period"] = period
        captured["benchmark"] = benchmark
        return {
            "period": period,
            "start": "2025-06-05",
            "end": "2026-06-05",
            "base_currency": "USD",
            "twr": {"period_return": 0.10, "annualized": 0.10},
            "mwr": {"xirr": 0.12},
            "period_gain_base": 1000.0,
            "start_value": 9000.0,
            "end_value": 10000.0,
            "data_quality": {
                "market_since": "2025-01-01",
                "value_points": 9,
                "sufficient": True,
                "has_unknown_cost": False,
            },
        }

    monkeypatch.setattr(returns_service, "portfolio_returns_with_benchmark", fake)
    return captured


def test_get_returns_in_registered_set():
    names = {t["name"] for t in TOOL_SCHEMAS}
    assert "get_returns" in names


def test_get_returns_returns_shape_and_no_proposed_change(monkeypatch):
    captured = _canned(monkeypatch)
    res, proposed = call("get_returns", period="1Y")
    assert proposed is None  # pure read
    assert captured["period"] == "1Y"
    assert {
        "period",
        "start",
        "end",
        "base_currency",
        "twr",
        "mwr",
        "period_gain_base",
        "data_quality",
    } <= set(res)
    assert res["twr"]["period_return"] == 0.10
    assert res["mwr"]["xirr"] == 0.12


def test_get_returns_defaults_period_when_missing(monkeypatch):
    captured = _canned(monkeypatch)
    res, proposed = call("get_returns")
    assert proposed is None
    assert captured["period"] == "1Y"
    assert res["period"] == "1Y"


def test_get_returns_unknown_period_coerces_to_1y(monkeypatch):
    captured = _canned(monkeypatch)
    call("get_returns", period="bogus")
    assert captured["period"] == "1Y"


def test_get_returns_passes_benchmark_through(monkeypatch):
    # P2-INV4: the optional benchmark arg flows to the composing service entrypoint, still a
    # pure read (proposed_change None). A blank/absent benchmark becomes None (no comparison).
    captured = _canned(monkeypatch)
    _, proposed = call("get_returns", period="1Y", benchmark="SPY")
    assert proposed is None
    assert captured["benchmark"] == "SPY"


def test_get_returns_blank_benchmark_is_none(monkeypatch):
    captured = _canned(monkeypatch)
    call("get_returns", period="1Y", benchmark="   ")
    assert captured["benchmark"] is None


def test_get_returns_passes_an_exact_window_and_by_holding(monkeypatch):
    seen = {}

    def fake(period="1Y", benchmark=None, today=None, **kwargs):
        seen.update(kwargs, period=period)
        return {"period": "CUSTOM", "holdings": [{"gain_base": float(i)} for i in range(45)]}

    monkeypatch.setattr(
        "backend.services.returns.portfolio_returns_with_benchmark", fake, raising=True
    )
    import asyncio
    import json

    from backend.ai.tools.portfolio import get_returns

    out, _ = asyncio.run(
        get_returns({"date_from": "2026-09-01", "date_to": "2026-09-30", "by_holding": True})
    )
    body = json.loads(out)
    assert seen["date_from"] == "2026-09-01" and seen["date_to"] == "2026-09-30"
    assert seen["by_holding"] is True
    # Bounded at the tool: the 20 best and 20 worst, and it says how many it left out.
    assert len(body["holdings"]) == 40 and body["holdings_omitted"] == 5


def test_get_returns_reports_a_bad_range_instead_of_raising():
    import asyncio
    import json

    from backend.ai.tools.portfolio import get_returns

    out, _ = asyncio.run(get_returns({"date_from": "2026-09-30", "date_to": "2026-09-01"}))
    assert "date_from must be on or before" in json.loads(out)["error"]
