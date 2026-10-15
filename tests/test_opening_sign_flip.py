"""Correcting an opening from negative to positive is a SIGN FLIP, not a transfer.

Repro (2026-08-09): a Flux opening booked at -640.00 had to become +58.20. A negative
opening is stored as a from-leg and a positive one as a to-leg, so the edit necessarily
switches legs — the form sends the new one while the row still carries the old, and the guard
read "both legs set" as an attempt to build a transfer. Every negative→positive repair was
refused, which is exactly the edit a damaged opening needs.
"""

from __future__ import annotations

import pytest

from backend.core.errors import ConflictError
from backend.services import ledger as ledger_service


@pytest.fixture
def account(db_isolated):
    acct = ledger_service.accounts.insert(
        {"id": "acc_rev", "name": "Flux", "kind": "asset", "type": "cash", "currency": "SGD"}
    )
    ledger_service.set_account_opening("acc_rev", amount=-640.00, as_of="2026-05-06")
    return acct


def _opening() -> dict:
    acct = ledger_service.accounts.find_by_id("acc_rev")
    return ledger_service.transactions.find_by_id(acct["opening_txn_id"])


def test_negative_opening_can_be_corrected_to_positive(account) -> None:
    row = _opening()
    assert row["from_account_id"] == "acc_rev" and row.get("to_account_id") in (None, "")

    ledger_service.update_transaction(
        row["id"], {"to_account_id": "acc_rev", "to_amount": 58.20, "to_currency": "SGD"}
    )

    after = _opening()
    assert after["to_account_id"] == "acc_rev"
    assert after["to_amount"] == 58.20
    assert not after.get("from_account_id")


def test_positive_opening_can_be_corrected_to_negative(account) -> None:
    row = _opening()
    ledger_service.update_transaction(
        row["id"], {"to_account_id": "acc_rev", "to_amount": 50.0, "to_currency": "SGD"}
    )
    ledger_service.update_transaction(
        _opening()["id"],
        {"from_account_id": "acc_rev", "from_amount": 20.0, "from_currency": "SGD"},
    )

    after = _opening()
    assert after["from_account_id"] == "acc_rev" and after["from_amount"] == 20.0
    assert not after.get("to_account_id")


def test_a_REAL_two_account_transfer_is_still_refused(account) -> None:
    """The guard's actual job: an opening must never become a two-leg movement."""
    ledger_service.accounts.insert(
        {"id": "acc_x", "name": "Other", "kind": "asset", "type": "cash", "currency": "SGD"}
    )
    with pytest.raises(ConflictError, match="single leg"):
        ledger_service.update_transaction(
            _opening()["id"],
            {"to_account_id": "acc_x", "to_amount": 5.0, "to_currency": "SGD"},
        )


def test_the_opening_keeps_its_id_so_the_account_pointer_survives(account) -> None:
    before = _opening()["id"]
    ledger_service.update_transaction(
        before, {"to_account_id": "acc_rev", "to_amount": 58.20, "to_currency": "SGD"}
    )
    assert ledger_service.accounts.find_by_id("acc_rev")["opening_txn_id"] == _opening()["id"]
