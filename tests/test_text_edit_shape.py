"""D7 — the EDIT shape for large stored text (RFC 2026-07-20 §14.2).

The load-bearing test in this file is ``test_lost_update_*``: a value that changes UNDERNEATH
the model between its read and its write must make the edit REFUSE, not clobber. Everything
else here (no-match, multi-match, an edit that lands, replace still working) is the machinery
that makes that guarantee real.
"""

from __future__ import annotations

import json

import pytest

from backend.core.text_edit import (
    TextEdit,
    TextEditError,
    apply_text_edit,
    edit_display,
    parse_text_edit,
    resolve_text_edits,
)

# ── The primitive ─────────────────────────────────────────────────────────────


def test_edit_that_lands_replaces_exactly_that_fragment():
    out = apply_text_edit(
        "You are a fraud agent.\nAlways notify on anomalies.\nBe terse.",
        TextEdit(old="Be terse.", new="Be thorough."),
        field="prompt",
        read_tool="list_agents(agent_id=…)",
    )
    assert out == "You are a fraud agent.\nAlways notify on anomalies.\nBe thorough."


def test_no_match_refuses_and_says_zero_and_names_the_read_tool():
    with pytest.raises(TextEditError) as exc:
        apply_text_edit(
            "You are a fraud agent.",
            TextEdit(old="Be terse.", new="Be thorough."),
            field="prompt",
            read_tool="list_agents(agent_id=…)",
        )
    msg = str(exc.value)
    assert "does not appear" in msg
    # S2 error contract: actionable — which field, and the tool that reads the current value.
    assert "`prompt`" in msg
    assert "list_agents(agent_id=…)" in msg
    assert "NOTHING was changed" in msg


def test_multiple_matches_refuses_and_reports_the_count():
    with pytest.raises(TextEditError) as exc:
        apply_text_edit(
            "Be terse. Then be terse. And be terse.",
            TextEdit(old="be terse.", new="be thorough."),
            field="prompt",
            read_tool="list_agents(agent_id=…)",
        )
    msg = str(exc.value)
    assert "appears 2 times" in msg
    assert "NOTHING was changed" in msg


def test_multiple_matches_never_silently_picks_the_first():
    """The dangerous failure this shape exists to prevent — no 'closest match', no first-wins."""
    text = "alpha DUP beta DUP gamma"
    with pytest.raises(TextEditError):
        apply_text_edit(
            text, TextEdit(old="DUP", new="X"), field="body", read_tool="list_skills(skill_id=…)"
        )


def test_no_fuzzy_fallback_whitespace_drift_is_a_refusal_not_a_guess():
    """A near-miss must fail loudly. Editing text the caller did not name is the whole risk."""
    with pytest.raises(TextEditError):
        apply_text_edit(
            "Always  notify on anomalies.",  # two spaces
            TextEdit(old="Always notify on anomalies.", new="Never notify."),  # one
            field="prompt",
            read_tool="list_agents(agent_id=…)",
        )


def test_parse_distinguishes_edit_from_replace():
    assert parse_text_edit({"old": "a", "new": "b"}) == TextEdit("a", "b")
    # A plain string is a REPLACE and passes through — Edit AND Write, per D7.
    assert parse_text_edit("a whole new prompt") is None
    assert parse_text_edit(None) is None
    # A DICT IS NEVER A REPLACE. This asserted `is None` until the 2026-07-20 security
    # review: returning None sent the caller down the full-replace path, and
    # update_skill(changes={"body": {}}) then SILENTLY ERASED the whole body — {} is falsy,
    # so db/skill_files.py's (record.get("body") or "").strip() produced "". Invisible on
    # web and connector alike, since the card showed only changed_fields:["body"].
    with pytest.raises(TextEditError):
        parse_text_edit({"something": "else"})
    with pytest.raises(TextEditError):
        parse_text_edit({})  # F1 — the erasure payload itself
    # F2: Claude Code's OWN parameter names, which this design is modelled on, making it the
    # most likely model mistake. Used to fall through to replace and then raise
    # AttributeError on .strip() — after the user had already approved the card.
    with pytest.raises(TextEditError):
        parse_text_edit({"old_string": "a", "new_string": "b"})


def test_malformed_edit_raises_rather_than_storing_a_dict_as_the_body():
    with pytest.raises(TextEditError):
        parse_text_edit({"old": "a"})  # no `new`
    with pytest.raises(TextEditError):
        parse_text_edit({"old": 12, "new": "b"})  # non-string
    with pytest.raises(TextEditError):
        parse_text_edit({"old": "", "new": "b"})  # empty matches everywhere


def test_resolve_leaves_a_full_replace_byte_identical():
    changes = {"prompt": "a brand new prompt", "name": "Fraud"}
    out = resolve_text_edits(
        changes, {"prompt": "old"}, fields=("prompt",), read_tool="list_agents(agent_id=…)"
    )
    assert out == changes
    assert out is not changes  # never mutates the caller's dict


def test_edit_display_is_the_fragment_not_a_derived_diff():
    d = edit_display(TextEdit(old="Be terse.", new="Be thorough."))
    assert d["old"] == "Be terse."
    assert d["new"] == "Be thorough."
    assert d["old_truncated"] is False


def test_edit_display_reports_truncation_honestly():
    d = edit_display(TextEdit(old="x" * 900, new="y"), context=100)
    assert d["old_truncated"] is True
    assert d["old_chars"] == 900
    assert len(d["old"]) <= 100


# ── Agents: the write choke ───────────────────────────────────────────────────


# ── THE LOST UPDATE — the point of the lane ───────────────────────────────────


# ── The confirm card ──────────────────────────────────────────────────────────


def test_connector_renders_the_edit_fragment_not_just_changed_fields():
    """A connector must not approve blinder than web (ADR 2026-07-19-confirm-gate-surface-matrix)."""
    from backend.connectors.processor import format_change_summary

    summary = format_change_summary(
        {
            "type": "agent_update",
            "display": {
                "name": "Fraud Watch",
                "changed_fields": ["prompt"],
                "prompt_edit": {"old": "Be terse.", "new": "Be thorough."},
            },
        }
    )
    assert "Be terse." in summary
    assert "Be thorough." in summary
    assert "```" in summary  # aligned fenced fragment -> escaped <pre> on Telegram


def test_connector_fragment_cannot_break_out_of_its_code_fence():
    """A ``` inside a prompt would otherwise close the fence and let text escape the block."""
    from backend.connectors.processor import format_change_summary

    summary = format_change_summary(
        {
            "type": "agent_update",
            "display": {
                "name": "X",
                "changed_fields": ["prompt"],
                "prompt_edit": {"old": "```\nrogue", "new": "safe"},
            },
        }
    )
    assert "```\nrogue" not in summary
    assert summary.count("```") == 4  # exactly two well-formed fences


def test_connector_generic_path_renders_a_skill_body_edit():
    from backend.connectors.processor import format_change_summary

    summary = format_change_summary(
        {
            "type": "skill_update",
            "display": {
                "name": "monthly-close",
                "changed_fields": ["body"],
                "body_edit": {"old": "Step 3: reconcile", "new": "Step 3: reconcile and tag"},
            },
        }
    )
    assert "Step 3: reconcile and tag" in summary
    assert "old_truncated" not in summary  # not a flattened key=value blob


# ── Skills ────────────────────────────────────────────────────────────────────


@pytest.fixture()
def skill(tmp_path, monkeypatch):
    from backend.db import skill_store
    from backend.db.skill_store import SkillStore

    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    monkeypatch.setattr(skill_store, "SKILLS_DIR", skills_dir)
    monkeypatch.setattr(skill_store, "SKILLS_STATE_JSON", tmp_path / "skills_state.json")
    store = SkillStore()
    return store.create(
        {
            "name": "monthly-close",
            "body": "Step 1: fetch\nStep 2: categorize\nStep 3: reconcile",
            "allowed_tools": ["get_transactions"],
        }
    )


def test_skill_body_edit_lands(skill):

    from backend.ai.tools.skills import _apply_skill_change as apply_skill_change

    text, _ = apply_skill_change(
        {
            "type": "skill_update",
            "skill_id": skill["id"],
            "changes": {"body": {"old": "Step 3: reconcile", "new": "Step 3: reconcile + tag"}},
        }
    )
    result = json.loads(text)
    assert result["ok"] is True
    # SkillStore round-trips the body through SKILL.md, which normalises a trailing newline.
    assert result["skill"]["body"].rstrip("\n").endswith("Step 3: reconcile + tag")
    assert "Step 1: fetch" in result["skill"]["body"]


def test_skill_body_full_replace_still_works(skill):

    from backend.ai.tools.skills import _apply_skill_change as apply_skill_change

    text, _ = apply_skill_change(
        {
            "type": "skill_update",
            "skill_id": skill["id"],
            "changes": {"body": "A completely rewritten playbook."},
        }
    )
    assert json.loads(text)["skill"]["body"].rstrip("\n") == "A completely rewritten playbook."


def test_lost_update_skill_body_edited_underneath_refuses(skill):
    """The skill-body twin of the agent-prompt lost update."""

    from backend.ai.tools.skills import _apply_skill_change as apply_skill_change

    # The user rewrites step 3 while the model's edit is in flight.
    apply_skill_change(
        {
            "type": "skill_update",
            "skill_id": skill["id"],
            "changes": {"body": "Step 1: fetch\nStep 2: categorize\nStep 3: reconcile TWICE"},
        }
    )
    text, _ = apply_skill_change(
        {
            "type": "skill_update",
            "skill_id": skill["id"],
            "changes": {"body": {"old": "Step 3: reconcile\n", "new": "Step 3: skip\n"}},
        }
    )
    result = json.loads(text)
    assert result["ok"] is False
    assert "does not appear" in result["error"]
