"""Unit tests for standard skill validation and rendering."""

from __future__ import annotations

from backend.db.skill_store import STARTER_SKILLS
from backend.services import skills as svc


def test_validate_accepts_standard_instruction_skill():
    assert svc.validate({"name": "monthly-close", "description": "Close a month."}) == []


def test_validate_ignores_experimental_allowed_tools_as_runtime_policy():
    assert svc.validate({"allowed_tools": ["missing_tool"]}) == []


def test_render_is_canonical_skill_md_and_keeps_tokens_literal():
    skill = {
        "name": "literal-input",
        "description": "Keep syntax literal.",
        "body": "Keep $ARGUMENTS, $0, $name, and {{legacy}} literal.",
        "parameters": [{"name": "legacy"}],
        "arguments": ["name"],
    }
    rendered = svc.render(skill)
    assert rendered.startswith("---\nname: literal-input\n")
    assert "$ARGUMENTS, $0, $name, and {{legacy}}" in rendered
    assert "parameters" not in rendered
    assert "arguments" not in rendered


def test_to_list_row_returns_stored_standard_record_without_degradation_fields():
    row = svc.to_list_row({"id": "x", "name": "x", "allowed_tools": ["gone"]})
    assert row == {"id": "x", "name": "x", "allowed_tools": ["gone"]}


def test_starter_bodies_have_no_nonstandard_template_language():
    forbidden = ("$ARGUMENTS", "{{", "when_to_use")
    for skill in STARTER_SKILLS:
        body = str(skill.get("body") or "")
        assert not any(token in body for token in forbidden), skill["id"]
        assert "parameters" not in skill
        assert "arguments" not in skill
        assert "trigger" not in skill
        assert "allowed_tools" not in skill


def test_starter_bodies_have_no_hardcoded_currency():
    bad_tokens = ["USD", "SGD", "EUR", "VND", "£", "€"]
    for skill in STARTER_SKILLS:
        body = str(skill.get("body") or "")
        assert not any(token in body for token in bad_tokens), skill["id"]
