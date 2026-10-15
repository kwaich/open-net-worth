"""Regression (2026-10-13): Lucius told the owner their savings were "on track" because
(a) the system prompt stated `Monthly savings: 8,408 (48%)` — profile income − profile
expenses, never checked — while the ledger showed 2.1k/4.5k, and (b) get_commitments returned
two past-dated set-asides as plain `planned` rows, indistinguishable from money in hand.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from backend.ai.tools import execute_tool


@pytest.fixture
def isolated(tmp_path, db_isolated):
    return tmp_path


def call(tool, **inp):
    text, _ = asyncio.run(execute_tool(tool, inp))
    return json.loads(text)


def _commitment_with_plan(
    plan: list, actions: list | None = None, installments: list | None = None
) -> str:
    cid = call("create_commitment", name="Apt", currency="VND")["commitment"]["id"]
    call("set_funding_plan", commitment_id=cid, funding_plan=plan)
    if installments:
        call("set_commitment_installments", commitment_id=cid, installments=installments)
    if actions:
        call("add_commitment_actions", commitment_id=cid, actions=actions)
    return cid


def test_past_dated_planned_funding_is_flagged_not_realized(isolated):
    _commitment_with_plan(
        [
            {
                "type": "income",
                "label": "Set-aside Aug",
                "amount": 7653,
                "currency": "SGD",
                "available_date": "2000-08-31",
            },
            {
                "type": "income",
                "label": "Arrived",
                "amount": 100,
                "currency": "SGD",
                "available_date": "2000-07-31",
                "status": "done",
            },
            {
                "type": "income",
                "label": "Future",
                "amount": 5352,
                "currency": "SGD",
                "available_date": "2999-10-31",
            },
        ],
        actions=[
            {"label": "Apply for loan", "due_date": "2000-08-30", "status": "pending"},
            # "missed" = the user marked it not happening: closed, never "overdue".
            {"label": "Dropped step", "due_date": "2000-08-29", "status": "missed"},
        ],
        installments=[{"label": "4th", "due_date": "2000-09-30", "amount": 938478505}],
    )
    c = call("get_commitments")["commitments"][0]
    ex = c["plan_execution"]
    assert [f["label"] for f in ex["funding_not_realized"]] == ["Set-aside Aug"]
    assert ex["funding_not_realized_total_base"]["amount"] == pytest.approx(7653, rel=0.01)
    assert [a["label"] for a in ex["actions_overdue"]] == ["Apply for loan"]
    assert ex["installments_unpaid_past_due"][0]["currency"] == "VND"
    assert "never count it as banked" in ex["warning"]


def test_on_schedule_commitment_carries_no_plan_execution(isolated):
    _commitment_with_plan(
        [
            {
                "type": "income",
                "label": "Future",
                "amount": 5352,
                "currency": "SGD",
                "available_date": "2999-10-31",
            }
        ]
    )
    c = call("get_commitments")["commitments"][0]
    assert "plan_execution" not in c


def test_prompt_labels_stated_savings_and_shows_ledger_actuals(isolated, monkeypatch):
    from backend.ai import prompts

    monkeypatch.setattr(
        prompts,
        "read_profile",
        lambda: {"base_currency": "SGD", "monthly_net_income": 16500, "monthly_expenses": 9000},
    )
    monkeypatch.setattr(
        "backend.services.cashflow.ledger_monthly_actuals",
        lambda base: {"months": 3, "income": 23000.0, "spending": 19900.0, "net": 3100.0},
    )
    prompt = prompts.build_system_prompt()
    assert "SGD 16,500/mo (stated)" in prompt
    assert "a target, not a measurement" in prompt
    assert "income SGD 23,000, spending SGD 19,900, net SGD 3,100" in prompt


def test_prompt_with_empty_ledger_says_so(isolated, monkeypatch):
    from backend.ai import prompts

    monkeypatch.setattr(
        prompts,
        "read_profile",
        lambda: {"base_currency": "SGD", "monthly_net_income": 5000, "monthly_expenses": 3000},
    )
    assert "no ledger history yet" in prompts.build_system_prompt()
