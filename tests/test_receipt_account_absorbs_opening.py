"""Creating accounts reads as accounts on the turn's receipt, not as their Starting Balances.

QA 2026-10-12: onboarding added six accounts and the chip said "6 transactions added" — each
account's Starting Balance row is booked through the ledger choke, which is all the receipt
saw. The account-create choke now records the account and absorbs its own opening row; a real
transaction in the same turn still counts.
"""

from __future__ import annotations

from backend.core import write_receipt as wr
from backend.services import accounts as accounts_service
from backend.services import ledger as ledger_service
from backend.services.write_receipt import build_turn_receipt


def test_new_accounts_read_as_accounts_and_real_spending_still_counts(db_isolated, monkeypatch):
    monkeypatch.setattr(ledger_service, "base_currency", lambda: "SGD")
    token = wr.open_receipt()
    try:
        ids = [
            accounts_service.create_account(
                {
                    "name": f"Synthetic {i}",
                    "type": "cash",
                    "currency": "SGD",
                    "initial_balance": 100,
                }
            )["id"]
            for i in range(6)
        ]
        ledger_service.create_transaction(
            {
                "kind": "expense",
                "date": "2026-10-01",
                "description": "Corner Cafe",
                "from_account_id": ids[0],
                "from_amount": 4.5,
                "from_currency": "SGD",
            }
        )
        receipt = build_turn_receipt(wr.collected())
    finally:
        wr.close_receipt(token)
    assert {g["type"]: g["count"] for g in receipt["groups"]} == {"account": 6, "transaction": 1}
    assert receipt["created"] == 7
