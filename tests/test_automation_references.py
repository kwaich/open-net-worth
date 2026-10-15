"""The save choke turns the subjects a trigger names into ids — for Lucius and the editor alike.

An account, a payee or a ticker may be named the way a person would ("Grab"); it resolves by id
or exact label, and an unknown or ambiguous one is refused rather than stored to never match.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from backend.ai.tools.automations import create_automation
from backend.core.errors import ValidationError

OPTIONS = {
    "accounts": [
        {"id": "acc_checking", "label": "Meridian Bank Checking"},
        {"id": "acc_savings", "label": "Meridian Bank Savings"},
    ],
    "instruments": [{"id": "instrument_aapl", "label": "AAPL"}],
}
REFERENCES = {
    "accounts": OPTIONS["accounts"],
    "payees": [
        {"id": "pay_grab", "label": "Grab"},
        {"id": "pay_uber", "label": "Uber"},
        {"id": "pay_grab_sg", "label": "GRAB SG"},
        {"id": "pay_grab_sg2", "label": "grab sg"},
    ],
}


def test_create_tool_forces_lucius_execution(monkeypatch):
    import backend.services.automations.service as service

    seen: dict = {}

    def apply(change: dict) -> dict:
        seen.update(change)
        return change["automation"]

    monkeypatch.setattr(service, "apply_automation_change", apply)
    text, proposed = asyncio.run(
        create_automation(
            {
                "name": "Morning brief",
                "instructions": "Review cash.",
                "trigger": {"kind": "manual"},
                "execution": {"kind": "agent", "agent_id": "agent_hidden"},
            }
        )
    )

    assert json.loads(text)["automation"]["execution"] == {"kind": "lucius"}
    assert seen["automation"]["execution"] == {"kind": "lucius"}
    assert proposed is None


def test_service_save_choke_resolves_visible_event_subject(monkeypatch):
    import backend.services.automations.references as references
    from backend.services.automations.service import validate_automation

    monkeypatch.setattr(references, "automation_options", lambda: OPTIONS)
    result = validate_automation(
        {
            "name": "Cash floor",
            "instructions": "Warn me when Meridian Bank falls below 100.",
            "trigger": {
                "kind": "event",
                "event": "balance",
                "when": {
                    "account": "Meridian Bank Checking",
                    "direction": "below",
                    "amount": 100,
                },
                "limit": "each",
            },
        }
    )

    assert result["trigger"]["when"]["account"] == "acc_checking"


def test_service_save_choke_refuses_unresolved_event_subject(monkeypatch):
    import backend.services.automations.references as references
    from backend.services.automations.service import validate_automation

    monkeypatch.setattr(references, "automation_options", lambda: OPTIONS)
    with pytest.raises(ValidationError, match="Unknown account.*does not match"):
        validate_automation(
            {
                "name": "Cash floor",
                "instructions": "Warn me.",
                "trigger": {
                    "kind": "event",
                    "event": "balance",
                    "when": {
                        "account": "Unknown account",
                        "direction": "below",
                        "amount": 100,
                    },
                    "limit": "each",
                },
            }
        )


def test_service_save_choke_accepts_a_freeform_price_ticker(monkeypatch):
    import backend.services.automations.references as references
    from backend.services.automations.service import validate_automation

    monkeypatch.setattr(references, "automation_options", lambda: OPTIONS)

    result = validate_automation(
        {
            "name": "NVDA price watch",
            "instructions": "Warn me when NVDA falls below 100.",
            "trigger": {
                "kind": "event",
                "event": "price",
                "when": {"symbol": "NVDA", "direction": "below", "value": 100},
                "limit": "each",
            },
        }
    )

    assert result["trigger"]["when"]["symbol"] == "NVDA"


def _txn(*conditions: dict) -> dict:
    return {
        "name": "Rides",
        "instructions": "Tell me about the ride.",
        "trigger": {
            "kind": "event",
            "event": "txn",
            "when": {"conditions": list(conditions)},
            "limit": "each",
        },
    }


@pytest.fixture
def references(monkeypatch):
    import backend.services.automations.references as references

    monkeypatch.setattr(references, "condition_references", lambda: REFERENCES)


def test_condition_payees_and_accounts_resolve_by_name(references):
    from backend.services.automations.service import validate_automation

    result = validate_automation(
        _txn(
            {"field": "payee", "op": "is", "value": ["grab", "pay_uber"]},
            {"field": "account", "op": "is_not", "value": "Meridian Bank Savings"},
        )
    )

    assert result["trigger"]["when"]["conditions"] == [
        {"field": "payee", "op": "is", "value": ["pay_grab", "pay_uber"]},
        {"field": "account", "op": "is_not", "value": "acc_savings"},
    ]


def test_a_name_and_its_id_are_one_payee(references):
    from backend.services.automations.service import validate_automation

    result = validate_automation(
        _txn({"field": "payee", "op": "is", "value": ["Grab", "pay_grab"]})
    )

    assert result["trigger"]["when"]["conditions"][0]["value"] == "pay_grab"


@pytest.mark.parametrize(
    ("condition", "message"),
    [
        (
            {"field": "payee", "op": "is", "value": "Gojek"},
            "'Gojek' does not match a persisted payee",
        ),
        ({"field": "payee", "op": "is", "value": "grab sg"}, "ambiguous"),
        ({"field": "account", "op": "is", "value": ["acc_checking", "Nowhere"]}, "'Nowhere'"),
    ],
)
def test_an_unknown_or_ambiguous_condition_subject_is_refused(references, condition, message):
    from backend.services.automations.service import validate_automation

    with pytest.raises(ValidationError, match=message):
        validate_automation(_txn(condition))


def test_a_name_that_resolves_into_a_contradiction_is_refused(references):
    """ "Grab" is not "pay_grab" as text, so only the resolved ids reveal the trap."""
    from backend.services.automations.service import validate_automation

    with pytest.raises(ValidationError, match="rule out every payee"):
        validate_automation(
            _txn(
                {"field": "payee", "op": "is", "value": "Grab"},
                {"field": "payee", "op": "is_not", "value": "pay_grab"},
            )
        )


def test_a_stored_id_whose_payee_is_gone_does_not_block_an_unrelated_edit(references):
    """Review 2026-10-11: a payee merged or deleted since must not refuse a rename or an effort
    change — the untouched trigger is re-validated on every save."""
    from backend.services.automations.service import validate_automation

    stale = _txn({"field": "payee", "op": "is", "value": ["pay_merged_away", "pay_uber"]})
    result = validate_automation({**stale, "name": "Renamed"}, previous_trigger=stale["trigger"])
    assert result["trigger"]["when"]["conditions"][0]["value"] == ["pay_merged_away", "pay_uber"]

    # A NEW value naming nothing is still refused, stale neighbour or not.
    with pytest.raises(ValidationError, match="'Gojek' does not match a persisted payee"):
        validate_automation(
            _txn({"field": "payee", "op": "is", "value": ["pay_merged_away", "Gojek"]}),
            previous_trigger=stale["trigger"],
        )


def test_a_name_and_its_id_in_two_conditions_are_one_payee_not_a_conflict(references):
    """Names resolve before the conflict check, so "Grab" and pay_grab meet as one payee."""
    from backend.services.automations.service import validate_automation

    result = validate_automation(
        _txn(
            {"field": "payee", "op": "is", "value": "Grab"},
            {"field": "payee", "op": "is", "value": "pay_grab"},
        )
    )
    assert [c["value"] for c in result["trigger"]["when"]["conditions"]] == ["pay_grab"] * 2
