"""Money-semantic regressions for durable Automation transaction events."""

from __future__ import annotations

import pytest

from backend.services.automations.events import _matches


@pytest.mark.parametrize(
    ("native_amount", "base_amount", "expected"),
    [
        (-1_000, -75, True),
        (-80, -125, False),
    ],
)
def test_a_threshold_on_one_account_uses_that_accounts_currency(
    native_amount: float, base_amount: float, expected: bool
) -> None:
    """ADR 2026-10-11: "min 100 on my USD account" means USD 100 — the editor shows the
    account's currency beside the box. (It used to compare the base-currency amount.)"""
    transaction = {
        "operation": "create",
        "kind": "expense",
        "from_account_id": "acc_usd",
        "from_amount": native_amount,
        "from_currency": "USD",
        "base_amount": base_amount,
        "base_currency": "SGD",
    }

    assert (
        _matches(
            "txn",
            {
                "account": "acc_usd",
                "direction": "debit",
                "min_amount": 100,
            },
            transaction,
        )
        is expected
    )
