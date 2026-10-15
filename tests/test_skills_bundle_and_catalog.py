"""Skill bundle viewer endpoint + system-prompt skills catalog (discover/auto-invoke).

  * GET /api/skills/{id}/bundle returns the on-disk script source for a kind=script skill
    (so the UI can SHOW the code), and 404s for a prompt skill / unknown id.
  * build_system_prompt injects an ENABLED skill's name + id into the "YOUR SKILLS" catalog
    (the discovery half of model-invoked skills), and omits a DISABLED one.

Offline; file I/O → test DATA_DIR (conftest). TestClient is loopback → auth-exempt.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.ai.prompts import _fmt_skills_catalog, build_system_prompt
from backend.ai.tools import execute_tool
from backend.db.skill_store import SkillStore
from backend.main import app

client = TestClient(app)


def _call(tool: str, **inp: Any):
    text, proposed = asyncio.run(execute_tool(tool, inp))
    return json.loads(text), proposed


_SRC = "import json, os\nprint(json.dumps({'ok': True}))\n"


def _make_script_skill() -> str:
    """Arrange a script-skill.

    ``apply_code_skill`` is deleted — the propose tool writes on approval (ADR
    2026-08-25-one-confirm-mechanism-for-every-tool). A bare test binds no card-capable
    surface, which reads as unattended, so this single call writes directly: exactly what
    setup wants, and the same path an Automation takes.
    """
    res, proposed = _call("save_code_as_skill", name="Bundle Viewer Demo", source=_SRC)
    assert proposed is None, "no card on an unattended surface"
    assert res["ok"], res
    return res["skill"]["id"]


def test_bundle_endpoint_returns_script_source():
    sid = _make_script_skill()
    r = client.get(f"/api/skills/{sid}/bundle")
    assert r.status_code == 200, r.text
    body = r.json()
    # The scripts/*.py source is present so the UI can render it.
    script_files = {k: v for k, v in body["files"].items() if k.startswith("scripts/")}
    assert script_files
    assert any("print(json.dumps" in src for src in script_files.values())
    # manifest is DERIVED from scripts/ — interpreter is correct, manifest.json not in files.
    assert body["manifest"]["scripts"][0]["interpreter"] == "python"
    assert "manifest.json" not in body["files"]


def test_bundle_endpoint_synthesizes_tree_for_prompt_skill():
    # Every skill exposes a full virtual tree now (no single-file vs bundled split). A prompt
    # skill → a plain SKILL.md (no onw.yaml); a caller's tool list is never written into it.
    skill = SkillStore().create({"name": "Plain", "allowed_tools": ["get_net_worth"], "body": "hi"})
    r = client.get(f"/api/skills/{skill['id']}/bundle")
    assert r.status_code == 200, r.text
    body = r.json()
    assert "name: plain" in body["skill_md"] and "hi" in body["skill_md"]
    assert "allowed-tools" not in body["skill_md"]
    assert "get_net_worth" not in body["skill_md"]
    assert "onw.yaml" not in body["files"]  # no sidecar in this layout
    assert "manifest.json" not in body["files"]  # prompt skill → no manifest file


def test_bundle_endpoint_404_unknown():
    assert client.get("/api/skills/sk-nope/bundle").status_code == 404


@pytest.mark.parametrize(
    ("method", "path", "kwargs"),
    [
        ("get", "/api/skills/bad_slug", {}),
        ("put", "/api/skills/bad_slug", {"json": {"changes": {"body": "x"}}}),
        ("delete", "/api/skills/bad_slug", {}),
        ("get", "/api/skills/bad_slug/bundle", {}),
        ("get", "/api/skills/bad_slug/export", {}),
    ],
)
def test_every_single_skill_route_maps_malformed_ids_to_422(method, path, kwargs):
    response = TestClient(app, raise_server_exceptions=False).request(method, path, **kwargs)

    assert response.status_code == 422
    assert "canonical slug" in response.json()["detail"]


def test_bulk_bundles_endpoint_returns_every_skill():
    # One request returns the full virtual tree for EVERY skill (eager prefetch — no per-skill
    # spinner). "bundles" must not be captured as a skill_id by the /{skill_id} route.
    store = SkillStore()
    a = store.create({"name": "Alpha", "allowed_tools": ["get_net_worth"], "body": "x"})
    sid = _make_script_skill()
    r = client.get("/api/skills/bundles")
    assert r.status_code == 200, r.text
    data = r.json()
    assert a["id"] in data and sid in data
    # Each entry is a full bundle view. ONW controls are in SKILL.md frontmatter (no onw.yaml).
    assert data[a["id"]]["skill_md"]
    assert "onw.yaml" not in data[a["id"]]["files"]
    # A caller's tool list is never written into a skill's SKILL.md
    assert "allowed-tools" not in data[a["id"]]["skill_md"]
    assert any(k.startswith("scripts/") for k in data[sid]["files"])


def test_prompt_catalog_lists_enabled_skill():
    SkillStore().create(
        {
            "name": "Quarterly Review Routine",
            "allowed_tools": ["get_net_worth"],
            "body": "do the review",
            "description": "Walk through the quarterly portfolio review",
            "enabled": True,
        }
    )
    prompt = build_system_prompt()
    assert "YOUR SKILLS" in prompt
    # Names are normalized to the standard lowercase-hyphenated slug on write.
    assert "quarterly-review-routine" in prompt


def test_prompt_catalog_omits_disabled_skill():
    SkillStore().create(
        {
            "name": "Disabled Routine XYZ",
            "allowed_tools": ["get_net_worth"],
            "body": "x",
            "enabled": False,
        }
    )
    prompt = build_system_prompt()
    assert "Disabled Routine XYZ" not in prompt


def test_prompt_catalog_keeps_more_than_40_names_and_description_text():
    store = SkillStore()
    for index in range(42):
        store.create(
            {
                "name": f"catalog-skill-{index:02d}",
                "body": "Use the records.",
                "description": (
                    f"Inspect the monthly records. Use when catalog trigger {index:02d} "
                    "is requested."
                ),
                "enabled": True,
            }
        )

    catalog = _fmt_skills_catalog()

    assert "- catalog-skill-41:" in catalog
    assert "(skill id: catalog-skill-41)" in catalog
    assert "Use when catalog trigger 41 is requested." in catalog


def test_build_system_prompt_uses_explicit_selected_model_for_catalog_budget(monkeypatch):
    from backend.ai.models import price_map

    selected_contexts = {
        ("small-selected", "https://small.example/v1"): 1_000,
        ("large-selected", "https://large.example/v1"): 1_000_000,
    }

    def context_window(model, base_url="", fallback=200_000):
        return selected_contexts[(model, base_url)]

    monkeypatch.setattr(price_map, "max_input_tokens", context_window)
    SkillStore().create(
        {
            "name": "selected-budget-skill",
            "body": "Use the records.",
            "description": "SELECTED-MODEL-TRIGGER " + "x" * 300,
            "enabled": True,
        }
    )

    small = build_system_prompt(model="small-selected", base_url="https://small.example/v1")
    large = build_system_prompt(model="large-selected", base_url="https://large.example/v1")

    assert "(skill id: selected-budget-skill)" in small
    assert "SELECTED-MODEL-TRIGGER" not in small
    assert "x" * 300 not in small
    assert "SELECTED-MODEL-TRIGGER" in large
    assert "x" * 300 in large
