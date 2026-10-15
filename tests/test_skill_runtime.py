"""Runtime contract for turn-local Agent Skills with Claude Code compatibility."""

from __future__ import annotations

import json

import pytest

from backend.ai import skill_runtime


def _skill(**overrides) -> dict:
    skill = {
        "id": "monthly-close",
        "name": "monthly-close",
        "description": "Close the books when the user asks for a monthly close.",
        "body": "Read the requested month from the conversation and review it.",
        "allowed_tools": ["get_transactions"],
        "enabled": True,
    }
    skill.update(overrides)
    return skill


def test_prepare_invocation_renders_claude_arguments_and_keeps_legacy_templates_literal():
    skill = _skill(
        body="Review $ARGUMENTS; first=$0; month=$month; keep {{legacy}} literal.",
        claude={"arguments": ["month"]},
    )
    entry = skill_runtime.prepare_invocation(
        skill_runtime.InvocationRequest(skill_id="monthly-close", argument_text='"May 2026" extra'),
        history=[],
        skill=skill,
    )

    assert entry["role"] == "user"
    # The rendered skill is the model's context: a tool list there reads as its whole toolset.
    assert "allowed-tools" not in entry["content"]
    assert 'Review "May 2026" extra; first=May 2026; month=May 2026' in entry["content"]
    assert "{{legacy}}" in entry["content"]
    assert entry["_onw_skill"]["full"] is True
    assert entry["_onw_skill"]["source"] == "user"


def test_prepare_invocation_does_not_carry_or_dedupe_across_turns():
    request = skill_runtime.InvocationRequest(skill_id="monthly-close")
    first = skill_runtime.prepare_invocation(request, history=[], skill=_skill())
    repeated = skill_runtime.prepare_invocation(request, history=[first], skill=_skill())
    assert repeated["content"] == first["content"]
    assert repeated["_onw_skill"]["full"] is True


def test_activation_marker_preserves_claude_arguments():
    payload = {
        skill_runtime.ACTIVATION_MARKER: {
            "skill_id": "monthly-close",
            "source": "model",
            "argument_text": "May 2026",
        }
    }
    assert skill_runtime.activation_from_tool_result(json.dumps(payload)) == (
        skill_runtime.InvocationRequest(
            skill_id="monthly-close", source="model", argument_text="May 2026"
        )
    )
    assert skill_runtime.activation_from_tool_result('{"message":"ordinary"}') is None


def test_invocation_source_rejects_unknown_values():
    with pytest.raises(ValueError, match="source"):
        skill_runtime.InvocationRequest(skill_id="monthly-close", source="system")


def test_argument_text_is_appended_when_skill_has_no_placeholders():
    entry = skill_runtime.prepare_invocation(
        skill_runtime.InvocationRequest(skill_id="monthly-close", argument_text="May 2026"),
        history=[],
        skill=_skill(body="Close the requested month."),
    )

    assert entry["content"].endswith("Close the requested month.\n\nARGUMENTS: May 2026\n")


def test_provider_projection_strips_host_metadata_and_merges_adjacent_user_messages():
    entry = skill_runtime.prepare_invocation(
        skill_runtime.InvocationRequest(skill_id="monthly-close"), history=[], skill=_skill()
    )
    projected = skill_runtime.provider_messages(
        [{"role": "user", "content": "close May", "ts": "private"}, entry]
    )
    assert projected == [{"role": "user", "content": f"close May\n\n{entry['content']}"}]


def test_catalog_contains_only_enabled_implicitly_invocable_skills():
    rendered = skill_runtime.catalog_text(
        [
            _skill(),
            _skill(id="disabled", name="disabled", enabled=False),
            _skill(
                id="explicit",
                name="explicit",
                openai={"policy": {"allow_implicit_invocation": False}},
            ),
            _skill(
                id="claude-manual",
                name="claude-manual",
                claude={"disable_model_invocation": True},
            ),
        ],
        model="",
        counts={},
    )
    assert "monthly-close" in rendered
    assert "disabled" not in rendered
    assert "explicit" not in rendered
    assert "claude-manual" not in rendered


def test_catalog_budget_is_two_percent_of_context_at_four_chars_per_token(monkeypatch):
    monkeypatch.setattr(skill_runtime.price_map, "max_input_tokens", lambda *_: 100_000)
    assert skill_runtime.catalog_budget_chars("model") == 8_000


def test_catalog_falls_back_to_8000_characters_when_context_unknown(monkeypatch):
    monkeypatch.setattr(skill_runtime.price_map, "max_input_tokens", lambda *_: 0)
    assert skill_runtime.catalog_budget_chars("model") == 8_000


def test_catalog_render_never_exceeds_the_computed_budget(monkeypatch):
    monkeypatch.setattr(skill_runtime.price_map, "max_input_tokens", lambda *_: 2_000)
    skills = [
        _skill(
            id=f"long-skill-{index}",
            name=f"long-skill-{index}",
            description="trigger " + ("x" * 1_000),
        )
        for index in range(20)
    ]

    rendered = skill_runtime.catalog_text(skills, model="small")

    skill_lines = rendered.split("### Available skills\n", 1)[1]
    assert len(skill_lines) + 1 <= skill_runtime.catalog_budget_chars("small")
