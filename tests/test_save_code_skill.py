"""Tests for the save-as-skill bridge (ad-hoc code execution C5): save_code_as_skill +
apply_code_skill.

Proves an AI-authored script-skill is materialized correctly. Execution requires the
instance-level kill switch in Settings → Developer — no per-skill gate fields. Offline,
no LLM. File I/O redirected to the test DATA_DIR by conftest.

Run: .venv/bin/pytest tests/test_save_code_skill.py -v
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from backend.ai.tools import execute_tool
from backend.db.skill_store import SkillStore
from backend.services.skill_bundles import version_dir


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


_SRC = "import json, os\nd = json.load(open(os.environ['SKILL_INPUT']))\nprint(json.dumps({'n': len(d)}))\n"


def test_save_code_as_skill_proposes():
    result, change = call(
        "save_code_as_skill", name="Subset Finder", source=_SRC, description="find subsets"
    )
    assert result is None, "a staged card pauses the tool_use — no result yet"
    assert change is not None
    assert change["type"] == "code_skill_create"
    assert change["script"]["source"] == _SRC.strip()
    assert change["display"]["name"] == "Subset Finder"
    # script id derived from the name (slugified)
    assert change["script"]["id"] == "subset_finder"


def test_save_requires_name_and_source():
    r1, c1 = call("save_code_as_skill", name="", source=_SRC)
    assert c1 is None and "name" in r1.get("error", "").lower()
    r2, c2 = call("save_code_as_skill", name="X", source="   ")
    assert c2 is None and "source" in r2.get("error", "").lower()


def test_apply_creates_script_skill_with_bundle():
    _, change = call("save_code_as_skill", name="Doubler", source=_SRC, description="doubles")
    result, _ = confirm_card(change)
    assert result.get("ok") is True, result
    created = result["skill"]

    # has_scripts is derived from the scripts/ folder presence.
    assert created["has_scripts"] is True
    # In file-first layout there is no bundle_version; scripts live in the skill folder.
    assert created.get("bundle_version") is None
    # No per-skill gate fields — permissions are instance-level (Settings → Developer).
    assert "enabled_script" not in created
    assert "allow_unattended" not in created

    # The script was materialized on disk in the skill's folder.
    vd = version_dir(created["id"], "live")
    assert (vd / "scripts" / "doubler.py").is_file()
    # No manifest.json on disk — manifest is derived from scripts/ on read.
    assert not (vd / "manifest.json").exists()

    # Re-read from the store confirms no per-skill gate fields.
    reread = SkillStore().find_by_id(created["id"])
    assert reread is not None
    assert reread["has_scripts"] is True
    assert "enabled_script" not in reread
    assert "allow_unattended" not in reread


def test_apply_rejects_empty_source():
    _, change = call("save_code_as_skill", name="Empty", source=_SRC)
    # Tampering with the card is INERT: approval runs the args the server stored, so a
    # blanked source here cannot reach the write. That is the whole security property of
    # staging (ADR 2026-08-25-one-confirm-mechanism-for-every-tool) — before it, the model
    # re-supplied these values on the confirming call and this test could meaningfully
    # corrupt them.
    change["script"]["source"] = "   "
    result, _ = confirm_card(change)
    assert result.get("ok") is True
    assert result["skill"]["id"]
