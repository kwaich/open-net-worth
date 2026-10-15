"""
AI tool-dispatch tests for the skill registry (RFC P2-SK1).

Offline + isolated: the SkillStore path is monkeypatched into tmp_path; execute_tool
constructs SkillStore() per call so the patch takes effect. execute_tool is async,
driven via asyncio.run. No LLM, no network.

Run:
    .venv/bin/pytest tests/test_skill_tools.py -v
"""

from __future__ import annotations

import asyncio
import json

import pytest

from backend.ai.skill_runtime import ACTIVATION_MARKER
from backend.ai.tools import TOOL_SCHEMAS, _skill_scope, execute_tool
from backend.core.errors import DataIntegrityError
from backend.db import skill_store
from backend.db.skill_store import STARTER_SKILLS, SkillStore


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    monkeypatch.setattr(skill_store, "SKILLS_DIR", skills_dir)
    monkeypatch.setattr(skill_store, "SKILLS_STATE_JSON", tmp_path / "skills_state.json")
    _skill_scope.clear_scope()
    yield tmp_path
    _skill_scope.clear_scope()


@pytest.fixture(autouse=True)
def _card_capable(monkeypatch):
    """These tests drive the propose→confirm flow, so they need a surface that CAN show a
    card. A bare unit test binds no cost surface, which reads as unattended — i.e. write
    immediately. Still yields to a REDEMPTION, because a ✓ must write rather than draw
    another card; stubbing that away would let these pass against a loop."""
    from backend.ai.tools import registry

    monkeypatch.setattr(
        registry, "should_preview_write", lambda: not registry.is_redeeming_approval()
    )


# A gated tool binds its confirm card to the tool_use_id of the call being gated, which a
# real turn always supplies to dispatch. Passing one here is that turn-loop binding.
_TOOL_USE_ID = "toolu_skill_test"


def call(tool, **inp):
    text, proposed = asyncio.run(execute_tool(tool, inp, tool_use_id=_TOOL_USE_ID))
    return (json.loads(text) if text is not None else None), proposed


def confirm_update(**inp):
    """update_skill's propose→confirm round trip in one call."""
    staged, proposed = call("update_skill", **inp)
    assert staged is None, "a staged card pauses the tool_use — no result yet"
    return confirm(proposed)


def confirm(proposed: dict):
    """Approve a staged card the way the SERVER does — re-run the staging tool on the args
    it stored. ``apply_skill_change`` is gone; there is no second tool to call."""
    return call(proposed["grant_tool"], confirm_token=proposed["tool_use_id"])


def test_all_skill_tools_registered():
    names = {t["name"] for t in TOOL_SCHEMAS}
    assert {
        "list_skills",
        "activate_skill",
        "create_skill",
        "update_skill",
        "delete_skill",
    } <= names
    # The propose tool writes on approval — its apply twin is deleted (ADR
    # 2026-08-25-one-confirm-mechanism-for-every-tool).
    assert "apply_skill_change" not in names


# ── list_skills (auto-tier) ─────────────────────────────────────────────────────


def test_list_skills_empty(isolated):
    rows, proposed = call("list_skills")
    assert rows == [] and proposed is None


def test_list_skills_rows_carry_no_tool_list(isolated):
    s = SkillStore()
    s.create({"name": "Good", "allowed_tools": ["get_portfolio"]})
    s.create({"name": "Bad", "allowed_tools": ["nonexistent_tool_xyz"]})
    rows, _ = call("list_skills")
    by_id = {r["id"]: r for r in rows}
    assert set(by_id) == {"good", "bad"}
    for row in by_id.values():
        assert "allowed_tools" not in row
        assert "degraded" not in row and "missing_tools" not in row


def test_legacy_tool_list_lines_never_reach_the_model_and_are_dropped_on_rewrite(isolated):
    """A skill seeded before starters stopped listing tools still has an ``allowed-tools:``
    line in its SKILL.md. A live chat read it as its whole toolset — four names, one of them
    loaded — and never called find_tools. Claude's ``disallowed-tools`` is the same kind of
    unenforced list. Both are dropped on read, absent from what activation injects, and gone
    from disk after the next write."""
    from backend.ai import skill_runtime

    SkillStore().create({"name": "budget-review", "description": "d", "body": "Do it."})
    md = isolated / "skills" / "budget-review" / "SKILL.md"
    legacy = md.read_text().replace(
        "description: d\n",
        "description: d\nallowed-tools: get_spending_summary get_profile\n"
        "disallowed-tools:\n- set_spending_limits\n",
    )
    assert "allowed-tools:" in legacy and "disallowed-tools:" in legacy
    md.write_text(legacy)

    skill = SkillStore().find_by_id("budget-review")
    assert skill["enabled"] is True
    assert "allowed_tools" not in skill
    assert "disallowed_tools" not in skill.get("claude", {})
    injected = skill_runtime.prepare_invocation(
        skill_runtime.InvocationRequest(skill_id="budget-review"), history=[], skill=skill
    )["content"]
    assert "Do it." in injected
    assert "allowed-tools" not in injected and "disallowed-tools" not in injected

    SkillStore().update_skill("budget-review", {"body": "Do it now."})
    assert "allowed-tools" not in md.read_text() and "disallowed-tools" not in md.read_text()


# ── activate_skill (auto-tier; instructions only; no write) ────────────────────


def test_activate_skill_returns_a_private_host_activation_without_narrowing_tools(isolated):
    s = SkillStore()
    s.create({"name": "Audit", "allowed_tools": ["get_portfolio", "get_transactions"]})

    async def _run_and_capture():
        text, proposed = await execute_tool("activate_skill", {"skill_id": "audit"})
        return json.loads(text), proposed, _skill_scope.offer_tool_names()

    res, proposed, scope = asyncio.run(_run_and_capture())
    assert proposed is None  # activate_skill never proposes a change
    activation = res[ACTIVATION_MARKER]
    assert activation["skill_id"] == "audit"
    assert activation["source"] == "model"
    assert "instructions" not in res
    assert scope is None


def test_activate_skill_unknown_id_returns_error(isolated):
    res, proposed = call("activate_skill", skill_id="ghost")
    assert "error" in res and proposed is None
    assert _skill_scope.offer_tool_names() is None  # no scope set on failure


@pytest.mark.parametrize("tool", ["list_skills", "activate_skill"])
def test_catalog_and_invocation_fail_closed_on_corrupt_skill_state(isolated, tool):
    """skills_state is DB-backed now (WAVE 5C) — a DB-backed store has no file-corruption
    failure mode to opt in or out of (skill_store._load_state's own docstring), so the
    fail-closed guard this pins is the row-SHAPE validation that replaces it: a malformed
    stored row (missing required fields) must still refuse to default-enable/overwrite
    runtime state rather than silently coercing it, exactly like the old corrupt-bytes case."""
    from backend.db.skill_state_store import SkillStateStore

    SkillStateStore().replace_all_keyed({"ghost": {"broken": True}})

    with pytest.raises(DataIntegrityError, match="skills_state failed validation"):
        call(tool, **({"skill_id": "ghost"} if tool == "activate_skill" else {}))


def test_activate_skill_ignores_legacy_tool_metadata(isolated):
    s = SkillStore()
    s.create({"name": "Bad", "allowed_tools": ["nonexistent_tool_xyz"]})
    res, _ = call("activate_skill", skill_id="bad")
    assert res["ok"] is True
    assert _skill_scope.offer_tool_names() is None


def test_activate_skill_does_not_interpret_legacy_parameters(isolated):
    s = SkillStore()
    s.create(
        {
            "name": "Param",
            "allowed_tools": ["get_portfolio"],
            "body": "{{x}}",
            "parameters": [{"name": "x", "required": True}],
        }
    )
    res, _ = call("activate_skill", skill_id="param")
    assert res["ok"] is True


def test_activate_skill_supports_many_skills_in_one_turn(isolated):
    s = SkillStore()
    s.create({"name": "Audit", "allowed_tools": ["get_portfolio"]})
    results = [call("activate_skill", skill_id="audit")[0] for _ in range(5)]
    assert all(result["ok"] is True for result in results)


def test_inline_boundary_clear_preserves_the_offer_without_blocking_skills(isolated):
    SkillStore().create({"name": "Audit", "allowed_tools": ["get_portfolio"]})
    _skill_scope.set_offer_scope(["get_portfolio"])

    _skill_scope.clear_inline_activation()
    res, _ = call("activate_skill", skill_id="audit")

    assert _skill_scope.offer_tool_names() == frozenset({"get_portfolio"})
    assert res["ok"] is True


def test_automatic_skill_chain_is_not_limited_to_two_skills(isolated):
    SkillStore().create({"name": "Audit", "allowed_tools": ["get_portfolio"]})

    allowed, _ = call("activate_skill", skill_id="audit")
    repeated, _ = call("activate_skill", skill_id="audit")

    assert ACTIVATION_MARKER in allowed
    assert ACTIVATION_MARKER in repeated


# ── create/update/delete (approve-tier: proposed_change non-None; AC-7) ─────────


def test_create_skill_returns_proposed_change(isolated):
    res, change = call(
        "create_skill",
        name="My Skill",
        description="Use when doing a thing.",
        body="Do a thing",
        allowed_tools=["get_portfolio"],
    )
    assert res is None
    assert change is not None and change["type"] == "skill_create"
    assert "allowed_tools" not in change["skill"]
    # Proposing does NOT write.
    assert SkillStore().all() == []


def test_create_skill_ignores_legacy_tool_arguments(isolated):
    res, change = call(
        "create_skill",
        name="X",
        description="Use for X.",
        allowed_tools=["totally_made_up"],
    )
    assert res is None
    assert change is not None
    assert "allowed_tools" not in change["skill"]


def test_update_skill_returns_proposed_change(isolated):
    s = SkillStore()
    s.create({"name": "X", "description": "Use for X.", "allowed_tools": ["get_portfolio"]})
    res, change = call("update_skill", skill_id="x", changes={"name": "X2"})
    assert res is None
    assert change["type"] == "skill_update" and change["skill_id"] == "x"
    assert change["changes"] == {"name": "X2"}


def test_update_skill_unknown_id_errors(isolated):
    res, change = call("update_skill", skill_id="ghost", changes={"name": "Z"})
    assert "error" in res and change is None


def test_delete_skill_returns_proposed_change(isolated):
    s = SkillStore()
    s.create({"name": "X", "allowed_tools": ["get_portfolio"]})
    res, change = call("delete_skill", skill_id="x")
    assert res is None
    assert change["type"] == "skill_delete" and change["skill_id"] == "x"
    # Still present — proposing does not delete.
    assert SkillStore().find_by_id("x") is not None


# ── the write half: approving a staged card re-runs the SAME tool ───────────────


def test_create_skill_writes_on_approval(isolated):
    staged, proposed = call(
        "create_skill",
        name="Made",
        description="Makes things",
        allowed_tools=["get_portfolio"],
        body="x",
    )
    assert staged is None, "a staged card pauses the tool_use — no result yet"
    res, after = confirm(proposed)
    assert res["ok"] is True and after is None, res
    assert res["skill"]["author"] == "lucius"  # authored via Lucius
    assert SkillStore().find_by_id(res["skill"]["id"]) is not None


def test_update_skill_writes_on_approval(isolated):
    s = SkillStore()
    s.create({"name": "X", "allowed_tools": ["get_portfolio"]})
    res, _ = confirm_update(skill_id="x", changes={"name": "X2"})
    assert res["ok"] is True
    assert SkillStore().find_by_id("x")["name"] == "x2"  # normalized to standard slug


def test_update_on_approval_drops_nonstandard_parameter_declarations(isolated):
    store = SkillStore()
    store.create(
        {
            "name": "Parameter Guard",
            "body": "month={{month}}",
            "parameters": [{"name": "month", "required": True}],
        }
    )

    result, _ = confirm_update(
        skill_id="parameter-guard",
        changes={"parameters": [{"name": "/second "}]},
    )

    assert result["ok"] is True
    assert "parameters" not in SkillStore().find_by_id("parameter-guard")


def test_delete_skill_writes_on_approval(isolated):
    s = SkillStore()
    s.create({"name": "X", "allowed_tools": ["get_portfolio"]})
    staged, proposed = call("delete_skill", skill_id="x")
    assert staged is None
    res, _ = confirm(proposed)
    assert res["ok"] is True
    assert SkillStore().find_by_id("x") is None


# ── AC-11: seeded starters validate against the LIVE registry, none degraded ────


def test_starters_list_without_nonstandard_degradation_projection(isolated):
    s = SkillStore()
    s.seed_starters()
    rows, _ = call("list_skills")
    assert len(rows) == len(STARTER_SKILLS)
    for r in rows:
        assert "degraded" not in r and "missing_tools" not in r


# ── H1 regression: AI tool cannot flip human-only gate fields ──────────────────
# enabled_script / allow_unattended / kind / bundle_version / bundle_sha must be
# stripped by the AI tool layer — the model may never set them via update_skill or
# the confirm path, regardless of what the input dict contains.
# Permissions now live at the instance config level (Settings → Developer), not on
# individual skill records. The AI tool strip is a defense-in-depth measure: even if
# a legacy caller sends these fields, the AI tool layer drops them silently.


def test_update_skill_tool_strips_legacy_gate_fields(isolated):
    """update_skill propose step strips enabled_script/allow_unattended from changes.

    These are now legacy no-op fields (permissions moved to instance config). The AI
    tool must still strip them so a model injection has zero effect.
    """
    s = SkillStore()
    sk = s.create(
        {
            "name": "My Skill",
            "description": "Use for this task.",
            "allowed_tools": ["get_portfolio"],
        }
    )
    sid = sk["id"]
    res, change = call(
        "update_skill",
        skill_id=sid,
        changes={"name": "Renamed", "enabled_script": True, "allow_unattended": True},
    )
    assert res is None
    # The proposed change must NOT contain the legacy gate fields.
    assert "enabled_script" not in change["changes"]
    assert "allow_unattended" not in change["changes"]
    # The name change is still present — only the forbidden keys were stripped.
    assert change["changes"].get("name") == "Renamed"


def test_the_confirm_path_strips_legacy_gate_fields(isolated):
    """The confirm path also strips enabled_script."""
    s = SkillStore()
    sk = s.create(
        {
            "name": "Prompt",
            "description": "Use for this prompt.",
            "allowed_tools": ["get_portfolio"],
        }
    )
    sid = sk["id"]
    res, _ = confirm_update(
        skill_id=sid,
        changes={"name": "Hacked", "enabled_script": True, "allow_unattended": True},
    )
    assert res["ok"] is True
    skill = SkillStore().find_by_id(sid)
    # Name was updated (normalized) but the legacy gate fields remain absent on the record.
    assert skill["name"] == "hacked"
    assert "enabled_script" not in skill
    assert "allow_unattended" not in skill


def test_update_skill_tool_cannot_change_bundle_fields(isolated):
    """The model cannot inject bundle/derived fields via update_skill."""
    s = SkillStore()
    sk = s.create(
        {
            "name": "Prompt",
            "description": "Use for this prompt.",
            "allowed_tools": ["get_portfolio"],
        }
    )
    sid = sk["id"]
    res, change = call(
        "update_skill",
        skill_id=sid,
        changes={
            "name": "Hacked",
            "has_scripts": True,
            "bundle_version": "../evil",
            "bundle_sha": "x",
        },
    )
    assert res is None
    # All bundle fields stripped; safe name change preserved.
    assert "has_scripts" not in change["changes"]
    assert "bundle_version" not in change["changes"]
    assert "bundle_sha" not in change["changes"]
    assert change["changes"].get("name") == "Hacked"


def test_script_skill_has_no_per_skill_gates(isolated):
    """Script skills no longer carry enabled_script / allow_unattended on their records.

    Permissions are at the instance config level (Settings → Developer), not per-skill.
    """
    s = SkillStore()
    sk = s.create({"name": "Script Skill", "allowed_tools": ["get_portfolio"]})
    sid = sk["id"]
    # Write a scripts/ dir so has_scripts is derived True.
    scripts_dir = isolated / "skills" / sid / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    (scripts_dir / "run.py").write_text("print(1)\n")
    sk = SkillStore().find_by_id(sid)
    assert sk["has_scripts"] is True
    # No per-skill gate fields on the record.
    assert "enabled_script" not in sk
    assert "allow_unattended" not in sk
    assert "scripts_sha" not in sk


# ── supporting files (references/, assets/) — agentskills.io progressive disclosure ──


@pytest.fixture
def bundles(isolated, monkeypatch):
    from backend.services import skill_bundles

    monkeypatch.setattr(skill_bundles, "SKILLS_DIR", isolated / "skills")
    return isolated / "skills"


def test_create_skill_writes_its_reference_and_asset_files_on_approval(bundles):
    files = {
        "references/fields.md": "# Fields\n\nRead when filling the template.",
        "assets/report.html": "<!doctype html><p>{{SUMMARY}}</p>",
    }
    staged, proposed = call(
        "create_skill",
        name="Quarter Report",
        description="Use this skill for a quarterly money report. Not for a single month.",
        body="Read `references/fields.md` when you fill `assets/report.html`.",
        files=files,
    )
    assert staged is None
    assert [f["path"] for f in proposed["display"]["files"]] == sorted(files)
    res, _ = confirm(proposed)
    assert res["ok"] is True, res
    folder = bundles / "quarter-report"
    assert (folder / "references" / "fields.md").read_text() == files["references/fields.md"]
    assert (folder / "assets" / "report.html").read_text() == files["assets/report.html"]


@pytest.mark.parametrize(
    "path",
    [
        "../escape.md",
        "/etc/passwd",
        "references/../../x.md",
        "scripts/run.py",
        "SKILL.md",
        "references/.hidden.md",
        "references/tool.py",
        "notes.md",
    ],
)
def test_a_file_outside_references_or_assets_is_refused(isolated, path):
    res, change = call(
        "create_skill",
        name="Bad",
        description="Use this skill for nothing.",
        body="x",
        files={path: "text"},
    )
    assert change is None and "error" in res


def test_update_skill_adds_and_removes_files_on_approval(bundles):
    SkillStore().create({"name": "Notes", "description": "Use for notes.", "body": "x"})
    res, _ = confirm_update(skill_id="notes", changes={"files": {"references/a.md": "first"}})
    assert res["ok"] is True, res
    assert (bundles / "notes" / "references" / "a.md").read_text() == "first"
    res, _ = confirm_update(skill_id="notes", changes={"files": {"references/a.md": None}})
    assert res["ok"] is True, res
    assert not (bundles / "notes" / "references" / "a.md").exists()
