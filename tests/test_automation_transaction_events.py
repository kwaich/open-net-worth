"""Deterministic transaction-event Automation characterization tests."""

from __future__ import annotations

import pytest

from backend.db.automation_store import AutomationStore
from backend.services.automations.transaction_events import (
    apply_transaction_automations,
    match_transaction_automation,
    rewrite_category_refs,
)


def transform_automation(
    *,
    automation_id: str = "automation_test",
    name: str = "Test transform",
    when: dict | None = None,
    set_: dict | None = None,
    priority: int = 100,
    stop: bool = True,
    override: bool = False,
    status: str = "active",
) -> dict:
    return {
        "id": automation_id,
        "name": name,
        "description": "",
        "instructions": "Categorize matching transactions.",
        "status": status,
        "trigger": {
            "kind": "event",
            "event": "txn",
            "when": dict(when or {"description_contains": "cafe"}),
            "limit": "each",
        },
        "execution": {
            "kind": "transaction_transform",
            "set": dict(set_ or {"category": "cat_food"}),
            "priority": priority,
            "stop": stop,
            "override_existing": override,
        },
        "created_at": "2026-08-02T00:00:00+00:00",
        "updated_at": "2026-08-02T00:00:00+00:00",
        "provenance": {},
        "stats": {},
    }


@pytest.mark.parametrize(
    ("when", "draft", "expected"),
    [
        ({"description_contains": "grab"}, {"description": "GRAB* RIDE"}, True),
        ({"keywords": ["grab", "uber"]}, {"description": "Uber pool"}, True),
        ({"description_regex": r"^stripe"}, {"description": "STRIPE*PAYMENT"}, True),
        ({"description_regex": "("}, {"description": "anything"}, False),
        ({"memo_contains": "#claim"}, {"description": "Taxi", "memo": "#CLAIM"}, True),
        ({"memo_contains": "taxi"}, {"description": "Taxi", "memo": ""}, False),
        ({"account_id": "acc_a"}, {"to_account_id": "acc_a"}, True),
        ({"kind": "income"}, {"kind": "expense"}, False),
        ({"direction": "debit"}, {"from_account_id": "acc_a"}, True),
        ({"direction": "debit"}, {"to_account_id": "acc_a"}, False),
        ({"direction": "credit"}, {"to_account_id": "acc_a"}, True),
        ({"direction": "credit"}, {"from_account_id": "acc_a"}, False),
        (
            {"direction": "debit"},
            {"from_account_id": "acc_a", "to_account_id": "acc_b", "kind": "transfer"},
            True,
        ),
        (
            {"direction": "credit"},
            {"from_account_id": "acc_a", "to_account_id": "acc_b", "kind": "transfer"},
            False,
        ),
        (
            {"currency": "SGD", "min_amount": 10, "max_amount": 20},
            {"from_amount": 15, "from_currency": "SGD", "base_amount": 1},
            True,
        ),
        (
            {"currency": "SGD", "min_amount": 10},
            {"from_amount": 15, "from_currency": "USD", "base_amount": 20},
            False,
        ),
        ({"min_amount": 10}, {"base_amount": 12}, True),
    ],
)
def test_transaction_automation_matcher_preserves_rule_grammar(when, draft, expected):
    assert match_transaction_automation(transform_automation(when=when), draft) is expected


def test_explicit_category_beats_a_non_override_transaction_automation():
    automation = transform_automation(set_={"category": "cat_food"}, override=False)
    row, matches = apply_transaction_automations(
        {"kind": "expense", "category": "cat_travel", "description": "Cafe"},
        [automation],
    )
    assert row["category"] == "cat_travel"
    assert [match.automation_id for match in matches] == ["automation_test"]
    assert matches[0].category_applied is False


def test_matching_stop_halts_lower_automation_when_action_is_blocked():
    high = transform_automation(
        automation_id="automation_high",
        priority=10,
        set_={"category": "cat_blocked"},
    )
    low = transform_automation(
        automation_id="automation_low",
        priority=20,
        override=True,
        set_={"category": "cat_wrong"},
    )
    row, matches = apply_transaction_automations(
        {"kind": "expense", "category": "cat_explicit", "description": "Cafe"},
        [low, high],
    )
    assert row["category"] == "cat_explicit"
    assert [match.automation_id for match in matches] == ["automation_high"]
    assert matches[0].category_applied is False


def test_actions_are_additive_idempotent_and_keep_memo_isolated():
    automation = transform_automation(
        set_={
            "tags": ["work", "meal"],
            "note_append": "auto-reviewed",
            "memo_append": "#claim",
        }
    )
    draft = {
        "kind": "expense",
        "description": "Cafe auto-reviewed",
        "memo": "Team lunch #claim",
        "tags": ["meal", "personal"],
    }
    row, matches = apply_transaction_automations(draft, [automation])
    assert row["description"] == "Cafe auto-reviewed"
    assert row["memo"] == "Team lunch #claim"
    assert row["tags"] == ["meal", "personal", "work"]
    assert [match.automation_id for match in matches] == ["automation_test"]


def test_priority_stop_and_override_are_deterministic():
    low = transform_automation(
        automation_id="automation_low",
        priority=200,
        set_={"category": "cat_low", "tags": ["low"]},
        override=True,
    )
    high = transform_automation(
        automation_id="automation_high",
        priority=10,
        stop=False,
        set_={"category": "cat_high", "tags": ["high"]},
    )
    row, matches = apply_transaction_automations(
        {"kind": "expense", "description": "Cafe"}, [low, high]
    )
    assert row["category"] == "cat_low"
    assert row["tags"] == ["high", "low"]
    assert [match.automation_id for match in matches] == ["automation_high", "automation_low"]
    assert [match.category_applied for match in matches] == [True, True]


def test_split_protects_category_but_allows_additive_tags():
    automation = transform_automation(set_={"category": "cat_food", "tags": ["reviewed"]})
    row, matches = apply_transaction_automations(
        {
            "kind": "expense",
            "description": "Cafe",
            "category": "split",
            "splits": [{"category": "cat_food", "amount": 10}],
        },
        [automation],
    )
    assert row["category"] == "split"
    assert row["tags"] == ["reviewed"]
    assert matches[0].category_from == "split"
    assert matches[0].category_to == "split"


def test_category_reference_rewrite_updates_migrated_lucius_metadata_and_instructions(
    db_isolated,
):
    store = AutomationStore()
    stored = store.create(
        {
            **transform_automation(set_={"category": "cat_old", "tags": ["meal"]}),
            "execution": {"kind": "lucius"},
            "provenance": {
                "lucius_execution_migration": {
                    "from_kind": "transaction_transform",
                    "transaction_event": {
                        "set": {"category": "cat_old", "tags": ["meal"]},
                        "priority": 10,
                        "stop": True,
                        "override_existing": False,
                    },
                }
            },
        }
    )

    assert rewrite_category_refs("cat_old", "cat_new") == 1

    updated = store.find_by_id(stored["id"])
    metadata = updated["provenance"]["lucius_execution_migration"]["transaction_event"]
    assert metadata["set"]["category"] == "cat_new"
    assert "cat_new" in updated["instructions"]
    assert "cat_old" not in updated["instructions"]

    assert rewrite_category_refs("cat_new", None) == 1
    deleted_ref = store.find_by_id(stored["id"])
    assert deleted_ref["status"] == "paused"
    assert (
        "category"
        not in deleted_ref["provenance"]["lucius_execution_migration"]["transaction_event"]["set"]
    )
    assert "cat_new" not in deleted_ref["instructions"]
