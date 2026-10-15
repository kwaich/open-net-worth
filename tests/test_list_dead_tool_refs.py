"""Unit tests for backend/scripts/list_dead_tool_refs.py (RFC connectors-as-data-sources §D).

Read-only sweep of a real owner's own automations/skills for a mention of a RETIRED tool
name. AutomationStore is DB-backed (db_isolated). SkillStore is file-backed against a
module-level SKILLS_DIR/SKILLS_STATE_JSON that tests/conftest.py's shared throwaway
DATA_DIR does NOT reset per test, so ``isolated_skills`` below repoints both at a fresh
tmp_path per test — the same pattern test_standard_skill_contract.py uses.
"""

from __future__ import annotations

import pytest

from backend.db import skill_store as skill_store_mod
from backend.db.automation_store import AutomationStore
from backend.db.skill_store import SkillStore
from backend.scripts.list_dead_tool_refs import find_dead_tool_refs


@pytest.fixture
def isolated_skills(tmp_path, monkeypatch):
    monkeypatch.setattr(skill_store_mod, "SKILLS_DIR", tmp_path / "skills")
    monkeypatch.setattr(skill_store_mod, "SKILLS_STATE_JSON", tmp_path / "skills_state.json")


def _automation(*, instructions: str, name: str = "Watch it") -> dict:
    return AutomationStore().create(
        {
            "name": name,
            "instructions": instructions,
            "status": "active",
            "trigger": {"kind": "manual"},
            "execution": {"kind": "lucius"},
        }
    )


def test_clean_instance_has_no_findings(db_isolated):
    assert find_dead_tool_refs() == []


def test_finds_a_retired_tool_named_in_an_automations_instructions(db_isolated):
    _automation(instructions="Call plaid_sync_now every morning and summarize the result.")

    findings = find_dead_tool_refs()

    assert len(findings) == 1
    finding = findings[0]
    assert finding["kind"] == "automation"
    assert finding["dead_tools"] == ["plaid_sync_now"]
    assert finding["suggested"]["plaid_sync_now"] == "plaid_fetch_transactions"


def test_does_not_false_positive_on_a_longer_identifier(db_isolated):
    """'connector_sync' must not match as a substring of a longer name such as
    'connector_sync_result' — the whole point of the word-boundary regex."""
    _automation(instructions="Read the connector_sync_result field and report it.")

    assert find_dead_tool_refs() == []


def test_finds_a_retired_tool_named_in_a_skills_body(db_isolated, isolated_skills):
    SkillStore().create(
        {
            "name": "Old Playbook",
            "description": "Legacy sync playbook.",
            "body": "Step 1: call ibkr_sync_now. Step 2: review the fills.",
        }
    )

    findings = find_dead_tool_refs()

    assert len(findings) == 1
    finding = findings[0]
    assert finding["kind"] == "skill"
    assert finding["dead_tools"] == ["ibkr_sync_now"]
    assert finding["suggested"]["ibkr_sync_now"] == "ibkr_fetch_activity"


def test_the_bulk_connector_sync_retirement_has_no_single_replacement(db_isolated):
    """connector_sync has no one-tool replacement (RFC §A) — the ledger records advisory
    text instead of a tool name; the script must still surface it, just with that text."""
    _automation(instructions="Run connector_sync across every provider nightly.")

    findings = find_dead_tool_refs()

    assert len(findings) == 1
    suggestion = findings[0]["suggested"]["connector_sync"]
    assert "fetch" in suggestion


def test_findings_across_both_automations_and_skills_are_all_reported(db_isolated, isolated_skills):
    _automation(instructions="Uses plaid_sync_now.", name="A")
    SkillStore().create(
        {
            "name": "B",
            "description": "b",
            "body": "Uses ynab_sync_now.",
        }
    )

    findings = find_dead_tool_refs()

    assert {f["kind"] for f in findings} == {"automation", "skill"}
    assert len(findings) == 2
