"""
Tests for the growth + commitments parameters added to simulate_cashflow:
  - expense_growth_pct
  - income_growth_pct
  - include_commitments

Strategy: monkeypatch build_projection to capture kwargs, so the test doesn't
need a real data store and runs offline. A second group patches at the tool's
lazy-import target so we can assert the exact call shape.

Run:
    .venv/bin/python3 -m pytest tests/test_tools_cashflow_params.py -v
"""

from __future__ import annotations

import asyncio
import json

import pytest

from backend.ai.tools import execute_tool


def call(tool: str, **inp):
    text, proposed = asyncio.run(execute_tool(tool, inp))
    return json.loads(text), proposed


# ── helpers ──────────────────────────────────────────────────────────────────


def _fake_projection(**kwargs) -> dict:
    """Return a minimal valid projection dict so the tool's response shape holds."""
    return {
        "cash_balance_base": 1000.0,
        "fixed_deposit_base": 0.0,
        "investments_base": 0.0,
        "liquid_balance_base": 1000.0,
        "include": {
            "fixed_deposits": bool(kwargs.get("include_fixed_deposits")),
            "investments": bool(kwargs.get("include_investments")),
        },
        "actual_income_avg": 5000.0,
        "actual_expenses_avg": 3000.0,
        "monthly_income_avg": kwargs.get("income_override") or 5000.0,
        "monthly_expenses_avg": kwargs.get("expense_override") or 3000.0,
        "income_runway_months": 5,
        "simulation_active": False,
        "baseline_source": "average",
        "months": [],
    }


# ── schema: new params present ───────────────────────────────────────────────


def test_simulate_cashflow_schema_has_new_params():
    from backend.ai.tools import TOOL_SCHEMAS

    schema = next(t for t in TOOL_SCHEMAS if t["name"] == "simulate_cashflow")
    props = schema["input_schema"]["properties"]
    assert "expense_growth_pct" in props
    assert "income_growth_pct" in props
    assert "include_commitments" in props
    assert props["expense_growth_pct"]["type"] == "number"
    assert props["income_growth_pct"]["type"] == "number"
    assert props["include_commitments"]["type"] == "boolean"


# ── growth params reach build_projection ─────────────────────────────────────


def test_expense_growth_pct_passed_to_build_projection(monkeypatch):
    captured = {}

    def fake_build(**kwargs):
        captured.update(kwargs)
        return _fake_projection(**kwargs)

    import backend.services.cashflow as cf_svc

    monkeypatch.setattr(cf_svc, "build_projection", fake_build)

    # Also stub the commitments service so the import doesn't fail
    import backend.services.commitments as commit_svc

    monkeypatch.setattr(commit_svc, "projected_events", lambda: None)

    result, _ = call("simulate_cashflow", expense_growth_pct=4.5)
    assert captured.get("expense_growth_pct") == pytest.approx(4.5)
    assert "income_runway_months" in result


def test_income_growth_pct_passed_to_build_projection(monkeypatch):
    captured = {}

    def fake_build(**kwargs):
        captured.update(kwargs)
        return _fake_projection(**kwargs)

    import backend.services.cashflow as cf_svc

    monkeypatch.setattr(cf_svc, "build_projection", fake_build)

    import backend.services.commitments as commit_svc

    monkeypatch.setattr(commit_svc, "projected_events", lambda: None)

    call("simulate_cashflow", income_growth_pct=2.0)
    assert captured.get("income_growth_pct") == pytest.approx(2.0)


def test_both_growth_params_together(monkeypatch):
    captured = {}

    def fake_build(**kwargs):
        captured.update(kwargs)
        return _fake_projection(**kwargs)

    import backend.services.cashflow as cf_svc

    monkeypatch.setattr(cf_svc, "build_projection", fake_build)

    import backend.services.commitments as commit_svc

    monkeypatch.setattr(commit_svc, "projected_events", lambda: None)

    call("simulate_cashflow", expense_growth_pct=3.0, income_growth_pct=5.0)
    assert captured.get("expense_growth_pct") == pytest.approx(3.0)
    assert captured.get("income_growth_pct") == pytest.approx(5.0)


# ── growth defaults to 0.0 when omitted ──────────────────────────────────────


def test_growth_defaults_to_zero_when_omitted(monkeypatch):
    captured = {}

    def fake_build(**kwargs):
        captured.update(kwargs)
        return _fake_projection(**kwargs)

    import backend.services.cashflow as cf_svc

    monkeypatch.setattr(cf_svc, "build_projection", fake_build)

    import backend.services.commitments as commit_svc

    monkeypatch.setattr(commit_svc, "projected_events", lambda: None)

    call("simulate_cashflow")
    assert captured.get("expense_growth_pct") == pytest.approx(0.0)
    assert captured.get("income_growth_pct") == pytest.approx(0.0)


# ── growth params are clamped to [0, 20] ─────────────────────────────────────


def test_growth_clamped_above_20(monkeypatch):
    captured = {}

    def fake_build(**kwargs):
        captured.update(kwargs)
        return _fake_projection(**kwargs)

    import backend.services.cashflow as cf_svc

    monkeypatch.setattr(cf_svc, "build_projection", fake_build)

    import backend.services.commitments as commit_svc

    monkeypatch.setattr(commit_svc, "projected_events", lambda: None)

    call("simulate_cashflow", expense_growth_pct=99.0, income_growth_pct=-5.0)
    assert captured.get("expense_growth_pct") == pytest.approx(20.0)
    assert captured.get("income_growth_pct") == pytest.approx(0.0)


# ── include_commitments=True calls projected_events ──────────────────────────


def test_include_commitments_true_calls_projected_events(monkeypatch):
    events_called = []

    def fake_events():
        events_called.append(True)
        return {"2026-08": [{"amount": -500, "label": "Rent"}]}

    import backend.services.cashflow as cf_svc
    import backend.services.commitments as commit_svc

    monkeypatch.setattr(commit_svc, "projected_events", fake_events)
    monkeypatch.setattr(cf_svc, "build_projection", lambda **kw: _fake_projection(**kw))

    call("simulate_cashflow", include_commitments=True)
    assert events_called, "projected_events should have been called"


# ── include_commitments=False skips projected_events ─────────────────────────


def test_include_commitments_false_skips_projected_events(monkeypatch):
    events_called = []

    def fake_events():
        events_called.append(True)
        return {}

    import backend.services.cashflow as cf_svc
    import backend.services.commitments as commit_svc

    monkeypatch.setattr(commit_svc, "projected_events", fake_events)
    monkeypatch.setattr(cf_svc, "build_projection", lambda **kw: _fake_projection(**kw))

    call("simulate_cashflow", include_commitments=False)
    assert not events_called, "projected_events must NOT be called when include_commitments=False"


# ── include_commitments defaults to True ─────────────────────────────────────


def test_include_commitments_defaults_to_true(monkeypatch):
    events_called = []

    def fake_events():
        events_called.append(True)
        return {}

    import backend.services.cashflow as cf_svc
    import backend.services.commitments as commit_svc

    monkeypatch.setattr(commit_svc, "projected_events", fake_events)
    monkeypatch.setattr(cf_svc, "build_projection", lambda **kw: _fake_projection(**kw))

    call("simulate_cashflow")  # omit include_commitments
    assert events_called, "projected_events should be called by default"


# ── extra_events=None passed when include_commitments=False ──────────────────


def test_extra_events_is_none_when_commitments_false(monkeypatch):
    captured = {}

    def fake_build(**kwargs):
        captured.update(kwargs)
        return _fake_projection(**kwargs)

    import backend.services.cashflow as cf_svc

    monkeypatch.setattr(cf_svc, "build_projection", fake_build)

    call("simulate_cashflow", include_commitments=False)
    assert captured.get("extra_events") is None


# ── existing params still work after the extension ───────────────────────────


def test_existing_params_still_work(monkeypatch):
    captured = {}

    def fake_build(**kwargs):
        captured.update(kwargs)
        return _fake_projection(**kwargs)

    import backend.services.cashflow as cf_svc

    monkeypatch.setattr(cf_svc, "build_projection", fake_build)

    import backend.services.commitments as commit_svc

    monkeypatch.setattr(commit_svc, "projected_events", lambda: None)

    result, _ = call(
        "simulate_cashflow",
        income_override=6000.0,
        expense_override=2500.0,
        months=6,
    )
    assert captured["income_override"] == pytest.approx(6000.0)
    assert captured["expense_override"] == pytest.approx(2500.0)
    assert captured["months"] == 6
    assert "income_runway_months" in result


# ── cash / fixed-deposit / investments breakdown (ADR 2026-09-07) ─────────────


def test_simulate_cashflow_echoes_the_projection_rungs_instead_of_refolding(monkeypatch):
    """The three rungs are read off the projection, which already folded them over the ONE book
    load it made. Re-folding them here cost three more full ledger scans, and a write landing
    between the two folds gave the model rungs that disagreed with the months beside them."""
    import backend.services.cashflow as cf_svc
    import backend.services.commitments as commit_svc

    def _refold(*_a, **_kw):
        raise AssertionError("simulate_cashflow must read the rungs off the projection")

    monkeypatch.setattr(
        cf_svc,
        "build_projection",
        lambda **kw: {
            **_fake_projection(**kw),
            "cash_balance_base": 226.0,
            "fixed_deposit_base": 12_569.0,
            "investments_base": 8_431.0,
        },
    )
    monkeypatch.setattr(commit_svc, "projected_events", lambda: None)
    monkeypatch.setattr(cf_svc, "cash_balance_base", _refold)
    monkeypatch.setattr(cf_svc, "fixed_deposit_base", _refold)
    monkeypatch.setattr(cf_svc, "investments_runway_base", _refold)

    result, _ = call("simulate_cashflow")
    assert result["cash_balance_base"] == pytest.approx(226.0)
    assert result["fixed_deposit_base"] == pytest.approx(12_569.0)
    assert result["investments_base"] == pytest.approx(8_431.0)


def test_include_fixed_deposits_and_investments_passed_to_build_projection(monkeypatch):
    captured = {}

    def fake_build(**kwargs):
        captured.update(kwargs)
        return _fake_projection(**kwargs)

    import backend.services.cashflow as cf_svc
    import backend.services.commitments as commit_svc

    monkeypatch.setattr(cf_svc, "build_projection", fake_build)
    monkeypatch.setattr(commit_svc, "projected_events", lambda: None)

    call("simulate_cashflow", include_fixed_deposits=True, include_investments=True)
    assert captured.get("include_fixed_deposits") is True
    assert captured.get("include_investments") is True


def test_include_fixed_deposits_and_investments_default_false(monkeypatch):
    captured = {}

    def fake_build(**kwargs):
        captured.update(kwargs)
        return _fake_projection(**kwargs)

    import backend.services.cashflow as cf_svc
    import backend.services.commitments as commit_svc

    monkeypatch.setattr(cf_svc, "build_projection", fake_build)
    monkeypatch.setattr(commit_svc, "projected_events", lambda: None)

    call("simulate_cashflow")
    assert captured.get("include_fixed_deposits") is False
    assert captured.get("include_investments") is False
