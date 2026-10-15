"""get_transactions resolves an account filter given by NAME, and never answers a name it
can't place with a silent zero.

QA, 2026-10-13: Lucius asked for transactions on "Lark Visa Revolution" (60 rows). The tool
matched the name against ids, found nothing, and returned "count: 0" with no error — so he
decided the 59 imported rows had vanished and went querying the database. The same request by
id returned all 60. Every other account-taking tool resolves names through the shared resolver
(id, or exact case-insensitive name; ambiguity raises); this one now does too.
"""

from __future__ import annotations

import json

import pytest

from backend.ai.tools import execute_tool
from backend.services import ledger as svc


@pytest.fixture(autouse=True)
def env(monkeypatch, db_isolated):
    monkeypatch.setattr(svc, "base_currency", lambda: "SGD")
    for acc_id, name in (
        ("acc_card", "Harbor Visa Rewards"),
        ("acc_card2", "Harbor Premier"),
        ("acc_dup1", "Joint Card"),
        ("acc_dup2", "Joint Card"),
    ):
        svc.accounts.insert(
            {"id": acc_id, "name": name, "kind": "liability", "type": "credit", "currency": "SGD"}
        )
    for desc, amount in (("Corner Cafe", 12.5), ("City Bus", 3.2)):
        svc.create_transaction(
            {
                "kind": "expense",
                "date": "2026-08-10",
                "description": desc,
                "from_account_id": "acc_card",
                "from_amount": amount,
                "from_currency": "SGD",
            }
        )


async def _get(**filters) -> dict:
    out, _ = await execute_tool("get_transactions", {"limit": 10, **filters})
    return json.loads(out)


@pytest.mark.asyncio
async def test_a_name_finds_the_same_rows_as_the_id():
    by_id = await _get(account_id="acc_card")
    by_name = await _get(account_id="harbor visa rewards")  # case-insensitive exact name
    assert by_id["summary"]["count"] == 2
    assert by_name["summary"]["count"] == 2


@pytest.mark.asyncio
async def test_names_resolve_inside_account_ids_too():
    out = await _get(account_ids=["Harbor Visa Rewards", "Harbor Premier"])
    assert out["summary"]["count"] == 2


@pytest.mark.asyncio
async def test_an_unknown_name_is_an_error_naming_the_close_ones_not_a_zero():
    out = await _get(account_id="Harbor")  # never fuzzy: two cards start with it
    assert "summary" not in out
    assert "not found" in out["error"]
    assert "Harbor Visa Rewards" in out["error"] or "Harbor Premier" in out["error"]


@pytest.mark.asyncio
async def test_a_name_two_accounts_share_is_an_error_not_a_guess():
    out = await _get(account_id="Joint Card")
    assert "summary" not in out
    assert "Joint Card" in out["error"]
