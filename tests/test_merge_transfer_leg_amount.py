"""A statement row merging into a transfer re-amounts the leg on ITS OWN account.

Regression: a Flux transfer booked at -214.35 while the statement showed -215.90 leaving
the account (a 1.55 fee the ledger entry missed). The off-by band offered "choose Link to
adopt the settled ones" and adopted nothing — `apply_import_merge` skipped every transfer
target outright. The other leg is a different account in a different currency and the
statement says nothing about it, so it must stay exactly as booked.
"""

from __future__ import annotations

import pytest

from backend.services import ledger as ledger_service


@pytest.fixture
def book(db_isolated, fx_rates):
    for acct_id, name, ccy in (
        ("acc_rev", "Flux", "SGD"),
        ("acc_sister", "Linh Pham", "VND"),
        ("acc_other", "Compass", "SGD"),
    ):
        ledger_service.accounts.insert(
            {"id": acct_id, "name": name, "kind": "asset", "type": "cash", "currency": ccy}
        )
    ledger_service.transactions.insert(
        {
            "id": "txn_tf",
            "date": "2026-07-15",
            "kind": "transfer",
            "description": "Flux transfer OUT to sister",
            "from_account_id": "acc_rev",
            "from_amount": 214.35,
            "from_currency": "SGD",
            "to_account_id": "acc_sister",
            "to_amount": 3_820_000.0,
            "to_currency": "VND",
        }
    )


def _statement_row(**over) -> dict:
    row = {
        "date": "2026-07-15",
        "kind": "expense",
        "description": "International Transfer to Linh Pham",
        "from_account_id": "acc_rev",
        "from_amount": 215.90,
        "from_currency": "SGD",
    }
    row.update(over)
    return row


def test_the_leg_on_the_statement_account_adopts_the_settled_amount(book) -> None:
    ledger_service.apply_import_merge("txn_tf", _statement_row(), facts="statement")

    after = ledger_service.transactions.find_by_id("txn_tf")
    assert after["from_amount"] == 215.90
    assert after["cleared"] is True


def test_the_OTHER_leg_is_untouched(book) -> None:
    """The statement says nothing about the receiving side — rewriting it would invent an
    amount, and on a cross-currency transfer, an FX rate."""
    ledger_service.apply_import_merge("txn_tf", _statement_row(), facts="statement")

    after = ledger_service.transactions.find_by_id("txn_tf")
    assert after["to_amount"] == 3_820_000.0
    assert after["to_currency"] == "VND"


def test_a_row_from_an_unrelated_account_changes_no_leg(book) -> None:
    """Only the leg whose account the statement IS gets re-amounted."""
    ledger_service.apply_import_merge(
        "txn_tf", _statement_row(from_account_id="acc_other"), facts="statement"
    )

    after = ledger_service.transactions.find_by_id("txn_tf")
    assert after["from_amount"] == 214.35 and after["to_amount"] == 3_820_000.0


def test_an_inbound_row_re_amounts_the_receiving_leg(book) -> None:
    row = {
        "date": "2026-07-15",
        "kind": "income",
        "description": "Inward transfer",
        "to_account_id": "acc_sister",
        "to_amount": 3_818_500.0,
        "to_currency": "VND",
    }
    ledger_service.apply_import_merge("txn_tf", row, facts="statement")

    after = ledger_service.transactions.find_by_id("txn_tf")
    assert after["to_amount"] == 3_818_500.0
    assert after["from_amount"] == 214.35


def test_amount_only_mode_still_leaves_a_transfer_alone(book) -> None:
    """Only the statement is authoritative enough to re-amount a booked movement."""
    ledger_service.apply_import_merge("txn_tf", _statement_row(), facts="amount_only")

    assert ledger_service.transactions.find_by_id("txn_tf")["from_amount"] == 214.35
