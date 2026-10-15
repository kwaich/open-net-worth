"""save_code_as_skill / apply_code_skill — the save-as-skill bridge (RFC C5).

Proves an ad-hoc script can become a reusable SCRIPT-SKILL: propose returns a
code_skill_create change carrying the source; apply materializes a bundle and creates
the skill record with has_scripts=True. Execution requires the instance-level kill
switch in Settings → Developer (no per-skill gate). Offline, no LLM. File I/O →
test DATA_DIR (conftest).

Run: .venv/bin/pytest tests/test_code_skill_tool.py -v
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from backend.ai.tools import execute_tool
from backend.db.skill_store import SkillStore
from backend.services.skill_bundles import read_bundle


@pytest.fixture(autouse=True)
def _card_capable(monkeypatch):
    """A card-capable surface — these tests drive the propose→confirm flow. Still yields to
    a REDEMPTION, so a ✓ writes rather than drawing another card."""
    from backend.ai.tools import registry

    monkeypatch.setattr(
        registry, "should_preview_write", lambda: not registry.is_redeeming_approval()
    )


def confirm_card(change: dict):
    """Approve a staged card the way the SERVER does — re-run the STAGING tool on the args
    it stored. ``apply_code_skill`` is deleted; there is no second tool to call (ADR
    2026-08-25-one-confirm-mechanism-for-every-tool)."""
    return call(change["grant_tool"], confirm_token=change["tool_use_id"])


def call(tool: str, **inp: Any):
    text, proposed = asyncio.run(execute_tool(tool, inp, tool_use_id="toolu_test"))
    return (json.loads(text) if text is not None else None), proposed


_SRC = (
    "import json, os\n"
    "data = json.load(open(os.environ['SKILL_INPUT']))\n"
    "print(json.dumps({'n': len(data.get('items', []))}))\n"
)


def test_propose_returns_code_skill_create():
    res, change = call(
        "save_code_as_skill",
        name="Item counter",
        source=_SRC,
        description="Count items in the input",
    )
    assert res is None, "a staged card pauses the tool_use — no result yet"
    assert change is not None
    assert change["type"] == "code_skill_create"
    # The source rides on the change (display.source for the card + script.source for apply).
    # The tool strips surrounding whitespace, so compare against the stripped source.
    assert change["script"]["source"] == _SRC.strip()
    assert change["display"]["source"] == _SRC.strip()
    assert "allowed_tools" not in change["skill"]


def test_propose_requires_name_and_source():
    res, change = call("save_code_as_skill", name="", source=_SRC)
    assert change is None and "name" in res["error"].lower()
    res, change = call("save_code_as_skill", name="X", source="")
    assert change is None and "source" in res["error"].lower()


def test_apply_creates_script_skill_with_bundle():
    _res, change = call("save_code_as_skill", name="Item counter", source=_SRC, description="count")
    res, _ = confirm_card(change)
    assert res.get("ok") is True, res

    created = res["skill"]
    sid = created["id"]

    # has_scripts is derived True from the scripts/ folder presence.
    assert created["has_scripts"] is True
    # In file-first layout there is no bundle_version; scripts live in the skill folder.
    assert created.get("bundle_version") is None
    # Permissions are instance-level (Settings → Developer), not per-skill — no gate fields.
    assert "enabled_script" not in created
    assert "allow_unattended" not in created

    # The record actually persisted correctly.
    stored = SkillStore().find_by_id(sid)
    assert stored is not None
    assert stored["has_scripts"] is True
    assert stored.get("bundle_version") is None
    assert "enabled_script" not in stored
    assert "allow_unattended" not in stored

    # The bundle materialized with the script under a valid, readable manifest.
    bundle = read_bundle(sid, "live")
    manifest = bundle["manifest"]
    script_ids = [s["id"] for s in manifest["scripts"]]
    assert script_ids and manifest["scripts"][0]["interpreter"] == "python"
    assert manifest["scripts"][0]["network"] is False


def test_the_write_rejects_a_blank_source():
    """``apply_code_skill`` is deleted — the write body is reached through the propose tool's
    approval. Called with a blank source it still refuses, and leaves nothing behind."""
    from backend.ai.tools.code import _apply_code_skill

    text, _ = _apply_code_skill(
        {
            "skill": {"name": "X", "allowed_tools": ["run_script"], "body": "b"},
            "script": {"id": "x", "source": "   "},
        }
    )
    res = json.loads(text)
    assert res.get("ok") is False
    # No dangling record left behind on the rejection path.
    assert not any(s.get("name") == "X" for s in SkillStore().all())
