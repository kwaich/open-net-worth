"""Statement-draft routing through transaction Automations."""

from __future__ import annotations

from backend.services.automations.transaction_events import (
    route_statement_draft_with_automations,
)
from tests.test_automation_transaction_events import transform_automation


def statement_expense(description: str) -> dict:
    return {
        "date": "2026-08-02",
        "kind": "expense",
        "description": description,
        "from_account_id": "acc_checking",
        "from_amount": 100,
        "from_currency": "SGD",
    }


def routing_automation(account_id: str, **kwargs) -> dict:
    return transform_automation(
        name="Savings transfer",
        when={"description_contains": "payment to savings"},
        set_={"route_to_account": account_id, **kwargs},
    )


def test_statement_routing_happens_before_transfer_leg_validation():
    draft, matches = route_statement_draft_with_automations(
        statement_expense("PAYMENT TO SAVINGS"),
        [routing_automation("acc_savings")],
    )
    assert draft["kind"] == "transfer"
    assert draft["to_account_id"] == "acc_savings"
    assert draft["to_amount"] == 100
    assert matches[0].phase == "statement_draft"


def test_income_statement_routing_is_direction_aware_and_applies_labels():
    draft, matches = route_statement_draft_with_automations(
        {
            "kind": "income",
            "description": "Payment to savings",
            "to_account_id": "acc_checking",
            "to_amount": 50,
            "to_currency": "SGD",
            "tags": ["existing"],
        },
        [routing_automation("acc_savings", category="cat_transfer", tags=["routed"])],
    )
    assert draft["from_account_id"] == "acc_savings"
    assert draft["to_account_id"] == "acc_checking"
    assert draft["category"] == "cat_transfer"
    assert draft["tags"] == ["existing", "routed"]
    assert [match.automation_id for match in matches] == ["automation_test"]


def test_routing_automation_is_inert_in_the_ledger_transform_phase():
    from backend.services.automations.transaction_events import apply_transaction_automations

    original = statement_expense("Payment to savings")
    row, matches = apply_transaction_automations(original, [routing_automation("acc_savings")])
    assert row == original
    assert matches == []
