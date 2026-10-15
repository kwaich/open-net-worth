import json

import pytest


@pytest.fixture(autouse=True)
def _iso(tmp_path, monkeypatch, db_isolated):
    """Isolate ledger stores so created accounts/txns don't leak into the shared DATA_DIR.
    accounts/transactions/audit are DB-backed now (plan 3 task 4) — db_isolated redirects
    them to a fresh, empty database."""


async def test_add_transaction_tool_flags_pre_start_leg():
    from backend.ai.tools import ledger as ledger_tools
    from backend.services import accounts as accounts_service

    acct = accounts_service.create_account(
        {
            "name": "ToolCard",
            "kind": "liability",
            "type": "credit",
            "currency": "SGD",
            "initial_balance": -100,
            "initial_balance_date": "2026-05-31",
        }
    )
    out, _ = await ledger_tools.add_transactions(
        {
            "date": "2026-04-05",
            "kind": "expense",
            "account_id": acct["id"],
            "amount": 50,
            "currency": "SGD",
        }
    )
    body = json.loads(out)
    assert body["ok"] is True, body
    warnings = body.get("warnings") or []
    assert any(w["type"] == "before_tracking_start" for w in warnings), body


async def test_add_transaction_tool_clean_leg_no_flag():
    from backend.ai.tools import ledger as ledger_tools
    from backend.services import accounts as accounts_service

    acct = accounts_service.create_account(
        {
            "name": "ToolCard2",
            "kind": "liability",
            "type": "credit",
            "currency": "SGD",
            "initial_balance": -100,
            "initial_balance_date": "2026-05-31",
        }
    )
    out, _ = await ledger_tools.add_transactions(
        {
            "date": "2026-06-05",
            "kind": "expense",
            "account_id": acct["id"],
            "amount": 50,
            "currency": "SGD",
        }
    )
    body = json.loads(out)
    assert body["ok"] is True, body
    assert not (body.get("warnings") or [])
