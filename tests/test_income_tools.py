"""
AI-tool dispatch tests for the income tools (P2-INV2).

Pin the registry snapshot growth + the APPROVE-TIER propose→apply contract for income,
mirroring tests/test_lots_tools.py. Offline, isolated to tmp_path, no LLM/network.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from backend.ai.tools import TOOL_SCHEMAS, execute_tool
from backend.ai.tools import registry as tool_registry
from backend.db.account_store import AccountStore
from backend.db.income_event_store import IncomeEventStore
from backend.db.instrument_store import InstrumentStore
from backend.db.position_store import PositionStore
from backend.db.transaction_store import TransactionStore

# A renamed tool name no longer resolves at runtime (ADR 2026-08-25-tool-names-are-final) —
# the migration's rename table is the only record that a rename happened.
from backend.services import income as svc
from backend.services import ledger as ledger_service
from backend.services import lots as lots_service

# propose_record_income is now an ALIAS of record_income (2026-07-19 propose_*/
# apply_* merge) — deliberately absent from this set, since aliases never appear in
# TOOL_SCHEMAS (zero prompt cost, see test_inv2_income_tools_in_registered_set).
_INV2_TOOLS = {
    "get_income",
    "get_income_forecast",
    "record_income",
    "delete_income",
}


# A gated tool binds its confirm card to the tool_use_id of the call being gated, which a
# real turn always supplies to dispatch. Passing one here is that turn-loop binding.
_TOOL_USE_ID = "toolu_test"


def call(tool, **inp):
    text, proposed = asyncio.run(execute_tool(tool, inp, tool_use_id=_TOOL_USE_ID))
    return (json.loads(text) if text is not None else None), proposed


def _preview(monkeypatch):
    """propose_record_income is now an ALIAS of record_income — the SAME tool
    previews or writes depending on should_preview_write(), not on which name it was
    called by. Call this in a test that wants the PREVIEW branch (a bare test call sets no
    cost surface, which fails OPEN to a direct write)."""
    monkeypatch.setattr(tool_registry, "should_preview_write", lambda: True)


def test_inv2_income_tools_in_registered_set():

    names = {t["name"] for t in TOOL_SCHEMAS}
    assert _INV2_TOOLS <= names


@pytest.fixture
def isolated(tmp_path, monkeypatch, db_isolated):
    # accounts/transactions are DB-backed now (plan 3 task 4); income_events too (plan 4a
    # task 3) — db_isolated covers all three.
    monkeypatch.setattr(svc, "events", IncomeEventStore())
    monkeypatch.setattr(svc, "instruments", InstrumentStore())
    monkeypatch.setattr(svc, "positions", PositionStore())
    monkeypatch.setattr(lots_service, "instruments", InstrumentStore())
    monkeypatch.setattr(ledger_service, "accounts", AccountStore())
    monkeypatch.setattr(ledger_service, "transactions", TransactionStore())
    monkeypatch.setattr(svc, "base_currency", lambda: "USD")
    monkeypatch.setattr(lots_service, "_load_rates", lambda: {"USD": 1.0})
    monkeypatch.setattr(lots_service, "base_currency", lambda: "USD")
    monkeypatch.setattr(ledger_service, "base_currency", lambda: "USD")
    monkeypatch.setattr(
        "backend.services.ledger.to_base", lambda a, c, b=None, rates=None: round(float(a or 0), 2)
    )
    return tmp_path


def _seed():
    inst = InstrumentStore().create(
        {"symbol": "ABC", "name": "ABC", "quote_currency": "USD", "price_source_id": "ABC"}
    )
    acct = AccountStore().create(
        {"name": "Cash", "kind": "asset", "type": "cash", "currency": "USD"}
    )
    pos = PositionStore().create(
        {"instrument_id": inst["id"], "account_id": acct["id"], "quantity": 100, "cost_basis": 10}
    )
    return inst, acct, pos


def test_propose_record_income_returns_change_and_writes_nothing(isolated, monkeypatch):
    _preview(monkeypatch)
    inst, acct, pos = _seed()
    res, change = call(
        "record_income",
        position_id=pos["id"],
        gross_amount=100.0,
        tax_withheld=10.0,
        currency="USD",
        account_id=acct["id"],
    )
    assert res is None
    assert change["type"] == "record_income"
    assert change["display"]["net_amount"] == 90.0
    assert change.get("idempotency_key")
    # Nothing written yet.
    assert IncomeEventStore().all() == []
    assert TransactionStore().all() == []


def test_propose_unknown_position_errors(isolated):
    res, change = call("record_income", position_id="ghost", gross_amount=10)
    assert "error" in res and change is None


def test_apply_record_income_writes(isolated):
    inst, acct, pos = _seed()
    res, change = call(
        "record_income",
        position_id=pos["id"],
        gross_amount=100.0,
        tax_withheld=10.0,
        currency="USD",
        account_id=acct["id"],
    )
    assert res["ok"] is True
    assert change is None
    assert len(IncomeEventStore().all()) == 1
    assert len(TransactionStore().all()) == 1


def test_delete_income_tool(isolated):
    inst, acct, pos = _seed()
    res, _ = call(
        "record_income",
        position_id=pos["id"],
        gross_amount=100.0,
        currency="USD",
        account_id=acct["id"],
    )
    out, _ = call("delete_income", event_id=res["event_id"])
    assert out["ok"] is True
    assert IncomeEventStore().all() == []
