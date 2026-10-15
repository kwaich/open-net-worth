"""Reopening a conversation rebuilds a paused call's confirm card AS THE LIVE TURN SHOWED IT.

A self-staging tool's card is a resolved preview — a sale's lots consumed and realized gain,
a transaction's resolved category and base amount — that the call's raw arguments cannot
reproduce. The reload used to rebuild every paused call as a generic "Tool permission" card
of raw JSON arguments, so a page reload (or opening the conversation on another client)
turned "Record sale · +$120 realized" into ``disposals {…json…}``. Approval still
re-verified the staged outcome, so it was a display defect, not a gate bypass.

The live client builds its card as ``{...event.change, id: event.tool_use_id}``
(``chatReducer``'s ``proposed_change`` branch); the rebuilt card must be that same dict.
"""

from __future__ import annotations

import asyncio

import pytest

from backend.ai import assistant
from backend.ai.tools import _confirm_gate, execute_tool
from backend.ai.tools import registry as tool_registry
from backend.core import chat_session, tool_use_context
from backend.db.pending_plan_store import PendingPlanStore
from backend.db.position_store import PositionStore
from backend.services import lots as lots_svc

SESSION = "rehydrate-self-staged"
TOOL_USE_ID = "toolu_rehydrate_1"


def _live_card(tool: str, inp: dict) -> dict:
    chat_session.set_session_id(SESSION)
    try:
        text, proposed = asyncio.run(execute_tool(tool, inp, tool_use_id=TOOL_USE_ID))
    finally:
        chat_session.clear_session_id()
    assert text is None and proposed is not None, text
    return {**proposed, "id": TOOL_USE_ID}


def _reloaded_card(monkeypatch, tool: str, inp: dict) -> dict:
    transcript = [
        {"role": "user", "content": "go"},
        {
            "role": "assistant",
            "content": [{"type": "tool_use", "id": TOOL_USE_ID, "name": tool, "input": inp}],
        },
    ]
    monkeypatch.setattr(assistant, "_load_session", lambda _sid: [dict(m) for m in transcript])
    monkeypatch.setattr(assistant, "_read_index", lambda: [])
    monkeypatch.setattr(assistant, "_rehydrated_turn_usage", lambda *a, **k: {})
    monkeypatch.setattr(
        "backend.services.automations.runs.automation_run_for_session", lambda _sid: None
    )
    last = assistant.rehydrate_messages(SESSION)[-1]
    assert last["awaitingApproval"] is True
    [card] = last["proposedChanges"]
    assert {"type": "change", "change": card} in last["blocks"]
    return card


@pytest.fixture
def preview(monkeypatch):
    monkeypatch.setattr(lots_svc, "_load_rates", lambda: {"USD": 1.0})
    monkeypatch.setattr(lots_svc, "base_currency", lambda: "USD")
    monkeypatch.setattr(tool_registry, "should_preview_write", lambda: True)


def test_a_staged_sale_reloads_as_the_same_record_sale_card(preview, monkeypatch):
    pos = PositionStore().create(
        {"instrument_id": "inst-1", "account_id": "acc-1", "quantity": 0, "cost_currency": "USD"}
    )
    lots_svc.apply_lot_change(
        {
            "type": "acquire",
            "position_id": pos["id"],
            "quantity": 10,
            "unit_cost": 100,
            "cost_currency": "USD",
            "acquired_at": "2026-01-01",
        }
    )
    inp = {
        "position_id": pos["id"],
        "quantity": 4,
        "proceeds_per_unit": 130,
        "proceeds_currency": "USD",
        "method": "fifo",
        "disposed_at": "2026-06-01",
    }
    live = _live_card("dispose_lots", inp)
    assert live["type"] == "lot_dispose"
    assert live["display"]["realized_gain"] == 120.00

    reloaded = _reloaded_card(monkeypatch, "dispose_lots", inp)

    assert reloaded == live
    assert reloaded["display"]["consumptions"][0]["qty"] == 4.0
    assert reloaded["grant_tool"] == "dispose_lots"


def test_a_generically_gated_call_reloads_as_the_card_it_staged(monkeypatch):
    """Still the raw-arguments shape — but the one it was STAGED with, preview included."""
    tool_use_context.set_tool_use_id(TOOL_USE_ID)
    chat_session.set_session_id(SESSION)
    try:
        _, change = _confirm_gate.stage_generic("telegram_send_message", {"message": "hi"})
    finally:
        tool_use_context.set_tool_use_id(None)
        chat_session.clear_session_id()
    live = {**change, "id": TOOL_USE_ID}

    reloaded = _reloaded_card(monkeypatch, "telegram_send_message", {"message": "hi"})

    assert reloaded == live
    assert reloaded["type"] == "tool_permission"
    assert reloaded["display"]["arguments"] == {"message": "hi"}


def test_an_entry_staged_before_the_card_was_kept_falls_back_to_raw_arguments(monkeypatch):
    """A row from before this change has no ``change`` — approvals never expire, so it can
    still be open. It keeps the old card rather than none at all."""
    PendingPlanStore().stage_pending(
        TOOL_USE_ID,
        "dispose_lots",
        {"position_id": "p1"},
        change_type="lot_dispose",
        outcome={"realized_gain": 1},
        session_id=SESSION,
    )

    reloaded = _reloaded_card(monkeypatch, "dispose_lots", {"position_id": "p1"})

    assert reloaded["type"] == "tool_permission"
    assert reloaded["display"] == {"tool": "dispose_lots", "arguments": {"position_id": "p1"}}
    assert reloaded["id"] == reloaded["tool_use_id"] == TOOL_USE_ID


def test_a_staged_ledger_write_reloads_as_its_resolved_card(monkeypatch):
    """The ledger gate stores only an outcome FINGERPRINT (category id, base amount, count) —
    the resolved row on the card was never recoverable from it, so the card itself is kept."""
    change = {
        "type": "txn_create",
        "display": {"payee": "Corner Grocer", "amount": -42.5, "category": "Groceries"},
        "category": {"id": "cat_000000000001"},
        "base": {"amount": -42.5},
    }
    inp = {"account": "Checking", "amount": -42.5, "payee": "Corner Grocer"}
    tool_use_context.set_tool_use_id(TOOL_USE_ID)
    chat_session.set_session_id(SESSION)
    try:
        _, staged = _confirm_gate.stage("add_transactions", inp, change)
    finally:
        tool_use_context.set_tool_use_id(None)
        chat_session.clear_session_id()

    reloaded = _reloaded_card(monkeypatch, "add_transactions", inp)

    assert reloaded == {**staged, "id": TOOL_USE_ID}
    assert reloaded["type"] == "txn_create"
    assert PendingPlanStore().get_pending(TOOL_USE_ID)["outcome"] == {
        "category": "cat_000000000001",
        "base_amount": -42.5,
        "count": None,
    }
