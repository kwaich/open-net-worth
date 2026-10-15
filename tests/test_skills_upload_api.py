"""Skills upload/export/template — pure SKILL.md frontmatter encoding (no onw.yaml).

A skill carries no tool list and no ONW-only controls: an imported ``allowed-tools`` line
and the retired ``metadata.onw-*`` keys are accepted and DROPPED — never stored, never
exported (ADR 2026-09-26-skills-carry-no-allowed-tools). (Legacy onw.yaml in a zip is
silently ignored, not an error.)

No sidecar → unscoped (all tools, confirm-gated).
Covers upload (prompt skill, legacy tool-list lines, references bundle, script auto-manifest,
invalid-tool rejection), export round-trip, and the template zip. Hermetic.

Run: .venv/bin/pytest tests/test_skills_upload_api.py -v
"""

from __future__ import annotations

import io
import json
import zipfile

import pytest
from fastapi.testclient import TestClient

from backend.main import app


@pytest.fixture
def client():
    return TestClient(app)


def _zip(files: dict[str, str | bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return buf.getvalue()


def _skill_md(
    name="my-skill",
    *,
    body="do it",
    license=None,
    allowed_tools: list[str] | None = None,
    blocked_tools: list[str] | None = None,
    parameters: list[dict] | None = None,
) -> str:
    """Build a SKILL.md with standard frontmatter + optional ONW extensions.

    ONW tool controls are embedded directly:
      - ``allowed_tools`` → ``allowed-tools`` (space-separated)
      - ``blocked_tools`` → ``metadata.onw-blocked-tools`` (space-separated)
    """
    lines = ["---", f"name: {name}", "description: x"]
    if license:
        lines.append(f"license: {license}")
    if allowed_tools:
        lines.append(f"allowed-tools: {' '.join(allowed_tools)}")
    if blocked_tools or parameters:
        lines.append("metadata:")
    if blocked_tools:
        lines.append(f"  onw-blocked-tools: {' '.join(blocked_tools)}")
    if parameters:
        lines.append(f"  onw-parameters: '{json.dumps(parameters, separators=(',', ':'))}'")
    lines.append("---")
    lines.append("")
    lines.append(body)
    lines.append("")
    return "\n".join(lines)


def _upload(client, zip_bytes: bytes, filename="skill.zip"):
    return client.post(
        "/api/skills/upload", files={"file": (filename, zip_bytes, "application/zip")}
    )


def _has_member(names, suffix: str) -> bool:
    # Export wraps every entry under a <name>/ dir (spec shape), so match by suffix.
    return any(n.endswith(suffix) for n in names)


def _read_member(zip_bytes: bytes, suffix: str) -> str:
    zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
    name = next(n for n in zf.namelist() if n.endswith(suffix))
    return zf.read(name).decode()


def test_add_starter_seeds_and_is_idempotent(client, monkeypatch):
    r = client.post("/api/skills/starters/skill-creator")
    assert r.status_code == 200 and r.json()["skill"]["id"] == "skill-creator"
    from backend.services import skill_creator_bundle

    calls = 0
    original_install = skill_creator_bundle.ensure_skill_creator_installed

    def tracking_install():
        nonlocal calls
        calls += 1
        return original_install()

    monkeypatch.setattr(skill_creator_bundle, "ensure_skill_creator_installed", tracking_install)
    assert client.post("/api/skills/starters/skill-creator").json().get("already") is True
    assert calls == 1


def test_upload_pure_standard_skill_is_unscoped_and_disabled(client):
    # A raw standard skill (SKILL.md only, NO onw.yaml) installs fine — unscoped.
    r = _upload(client, _zip({"SKILL.md": _skill_md("imported-audit")}))
    assert r.status_code == 200, r.text
    sk = r.json()["skill"]
    assert sk["name"] == "imported-audit"
    assert sk["enabled"] is False  # born disabled
    assert "allowed_tools" not in sk


def test_upload_drops_allowed_tools_and_onw_extensions(client):
    r = _upload(
        client,
        _zip(
            {
                "SKILL.md": _skill_md(
                    "scoped",
                    allowed_tools=["get_transactions"],
                    blocked_tools=["bulk_delete_transactions"],
                )
            }
        ),
    )
    assert r.status_code == 200, r.text
    sk = r.json()["skill"]
    assert "allowed_tools" not in sk
    assert "blocked_tools" not in sk


def test_plain_upload_preserves_claude_code_frontmatter_fields(client):
    md = """---
name: claude-inline-upload
description: x
when_to_use: Use when a month is named.
argument-hint: "[month]"
arguments:
  - month
disable-model-invocation: true
user-invocable: false
disallowed-tools:
  - get_transactions
---
body
"""

    response = _upload(client, _zip({"SKILL.md": md}))

    assert response.status_code == 200, response.text
    skill = response.json()["skill"]
    assert skill["claude"] == {
        "when_to_use": "Use when a month is named.",
        "argument_hint": "[month]",
        "arguments": ["month"],
        "disable_model_invocation": True,
        "user_invocable": False,
    }
    exported = client.get(f"/api/skills/{skill['id']}/export")
    exported_md = _read_member(exported.content, "SKILL.md")
    assert "disable-model-invocation: true" in exported_md
    assert "argument-hint: '[month]'" in exported_md
    # Claude's tool list is accepted on import and dropped — a skill carries no tool list.
    assert "disallowed-tools" not in exported_md


def test_create_skill_api_accepts_structured_claude_extensions(client):
    response = client.post(
        "/api/skills",
        json={
            "name": "claude-inline-api",
            "description": "x",
            "claude": {
                "when_to_use": "Use when asked.",
                "argument_hint": "[month]",
                "arguments": ["month"],
                "disable_model_invocation": True,
                "user_invocable": False,
                "disallowed_tools": ["get_transactions"],
            },
        },
    )

    assert response.status_code == 200, response.text
    skill = response.json()["skill"]
    assert skill["name"] == "claude-inline-api"
    assert skill["claude"]["when_to_use"] == "Use when asked."
    assert skill["claude"]["arguments"] == ["month"]
    assert skill["claude"]["user_invocable"] is False
    assert "disallowed_tools" not in skill["claude"]


def test_create_and_upload_require_the_standard_description(client):
    create = client.post("/api/skills", json={"name": "missing-description"})
    assert create.status_code == 422

    upload = _upload(
        client,
        _zip({"SKILL.md": "---\nname: missing-description\n---\nbody\n"}),
    )
    assert upload.status_code == 400
    assert "description" in upload.json()["detail"]


def test_create_and_update_skill_api_ignore_nonstandard_parameters(client):
    create = client.post(
        "/api/skills",
        json={
            "name": "unsafe-parameters",
            "description": "x",
            "parameters": [{"name": "/second ", "required": True}],
        },
    )
    assert create.status_code == 200
    assert "parameters" not in create.json()["skill"]

    valid = client.post(
        "/api/skills",
        json={"name": "safe-parameters", "description": "x", "parameters": [{"name": "month"}]},
    )
    assert valid.status_code == 200, valid.text
    update = client.put(
        "/api/skills/safe-parameters",
        json={"changes": {"parameters": [{"name": "month"}, {"name": "month"}]}},
    )
    assert update.status_code == 200
    assert "parameters" not in update.json()["skill"]


def test_upload_ignores_nonstandard_parameter_metadata(client):
    response = _upload(
        client,
        _zip(
            {
                "SKILL.md": _skill_md(
                    "unsafe-import",
                    parameters=[{"name": "with space", "required": True}],
                )
            }
        ),
    )

    assert response.status_code == 200
    assert "parameters" not in response.json()["skill"]


def test_upload_preserves_standard_openai_metadata(client):
    response = _upload(
        client,
        _zip(
            {
                "SKILL.md": _skill_md("explicit-only"),
                "agents/openai.yaml": (
                    "interface:\n  display_name: Explicit only\n"
                    "policy:\n  allow_implicit_invocation: false\n"
                ),
            }
        ),
    )
    assert response.status_code == 200, response.text
    skill = response.json()["skill"]
    assert skill["openai"]["policy"]["allow_implicit_invocation"] is False
    exported = client.get(f"/api/skills/{skill['id']}/export")
    assert "allow_implicit_invocation: false" in _read_member(
        exported.content, "agents/openai.yaml"
    )


def test_upload_legacy_onw_yaml_is_ignored(client):
    """A zip carrying a legacy onw.yaml succeeds — the file is silently discarded."""
    legacy_onw = "tools:\n  allow:\n    - get_transactions\n"
    r = _upload(client, _zip({"SKILL.md": _skill_md("legacy-skill"), "onw.yaml": legacy_onw}))
    # The zip succeeds; onw.yaml controls are ignored (no allowed_tools from sidecar)
    assert r.status_code == 200, r.text
    assert "allowed_tools" not in r.json()["skill"]


def test_upload_accepts_a_legacy_allowed_tools_line_and_drops_it(client):
    """A SKILL.md from another host may list tools: the upload succeeds, the list is gone."""
    md = "---\nname: bad-tool\ndescription: x\nallowed-tools: not_a_real_tool\n---\nbody\n"
    r = _upload(client, _zip({"SKILL.md": md}))
    assert r.status_code == 200, r.text
    assert "allowed_tools" not in r.json()["skill"]


def test_upload_requires_skill_md(client):
    # A zip with no SKILL.md (only other files) is rejected.
    assert _upload(client, _zip({"README.md": "not a skill"})).status_code == 400


def test_upload_rejects_non_zip(client):
    assert _upload(client, b"not a zip").status_code == 400


def test_upload_rejects_traversal(client):
    assert _upload(client, _zip({"../evil.md": "x", "SKILL.md": _skill_md()})).status_code == 400


def test_upload_prompt_with_references_makes_bundle(client):
    r = _upload(
        client, _zip({"SKILL.md": _skill_md("with-refs"), "references/guide.md": "# Guide\nhi"})
    )
    assert r.status_code == 200, r.text
    sk = r.json()["skill"]
    # In file-first layout there is no bundle_version — references live in the skill folder.
    assert sk.get("bundle_version") is None
    b = client.get(f"/api/skills/{sk['id']}/bundle")
    assert b.status_code == 200 and "references/guide.md" in b.json()["files"]


def test_upload_script_inferred_from_scripts_dir(client):
    # No onw.yaml + scripts/ present → derived as scripted (has_scripts); born disabled.
    src = "import json,os\nprint(json.dumps({'ok':True}))\n"
    r = _upload(client, _zip({"SKILL.md": _skill_md("imported-script"), "scripts/main.py": src}))
    assert r.status_code == 200, r.text
    sk = r.json()["skill"]
    assert sk["has_scripts"] is True
    assert sk["enabled"] is False
    # No per-skill gate fields — permissions are instance-level (Settings → Developer).
    assert "enabled_script" not in sk
    # In file-first layout there is no bundle_version; script is live in the folder.
    assert sk.get("bundle_version") is None


def test_export_never_carries_an_imported_allowed_tools_line(client):
    """Upload a SKILL.md listing tools → export → no allowed-tools line → re-upload is clean."""
    up = _upload(
        client,
        _zip({"SKILL.md": _skill_md("round-trip", allowed_tools=["get_transactions"])}),
    )
    sid = up.json()["skill"]["id"]
    ex = client.get(f"/api/skills/{sid}/export")
    assert ex.status_code == 200 and ex.headers["content-type"] == "application/zip"
    names = zipfile.ZipFile(io.BytesIO(ex.content)).namelist()
    # Export: SKILL.md present, no onw.yaml (it doesn't exist in the new layout)
    assert _has_member(names, "SKILL.md") and not _has_member(names, "onw.yaml")
    skill_md_content = _read_member(ex.content, "SKILL.md")
    assert "allowed-tools" not in skill_md_content
    re_up = _upload(client, ex.content)
    assert re_up.status_code == 200, re_up.text
    assert "allowed_tools" not in re_up.json()["skill"]


def test_export_pure_skill_has_no_onw_artifacts(client):
    """A pure (unscoped) skill export: only SKILL.md, no onw.yaml, no extra artifacts."""
    up = _upload(client, _zip({"SKILL.md": _skill_md("pure")}))
    ex = client.get(f"/api/skills/{up.json()['skill']['id']}/export")
    names = zipfile.ZipFile(io.BytesIO(ex.content)).namelist()
    assert _has_member(names, "SKILL.md") and not _has_member(names, "onw.yaml")


def test_upload_preserves_license_and_attribution_files(client):
    # An Apache-licensed skill: license frontmatter + LICENSE.txt + NOTICE must survive install.
    files = {
        "SKILL.md": _skill_md("apache-skill", license="Complete terms in LICENSE.txt"),
        "LICENSE.txt": "Apache License, Version 2.0\n...full text...\n",
        "NOTICE": "apache-skill\nCopyright 2026 Someone\n",
    }
    r = _upload(client, _zip(files))
    assert r.status_code == 200, r.text
    sk = r.json()["skill"]
    assert sk["license"] == "Complete terms in LICENSE.txt"
    # In file-first layout there is no bundle_version; files live in the skill folder.
    assert sk.get("bundle_version") is None
    b = client.get(f"/api/skills/{sk['id']}/bundle").json()
    assert "LICENSE.txt" in b["files"] and "NOTICE" in b["files"]
    assert "Apache License" in b["files"]["LICENSE.txt"]


def test_export_roundtrips_license_and_notice(client):
    up = _upload(
        client,
        _zip(
            {
                "SKILL.md": _skill_md("round-lic", license="Apache-2.0"),
                "NOTICE": "round-lic\nCopyright 2026 Acme\n",
            }
        ),
    )
    sid = up.json()["skill"]["id"]
    ex = client.get(f"/api/skills/{sid}/export")
    assert ex.status_code == 200
    assert _has_member(zipfile.ZipFile(io.BytesIO(ex.content)).namelist(), "NOTICE")
    assert "license: Apache-2.0" in _read_member(ex.content, "SKILL.md")
    # Re-upload preserves both license + NOTICE → full round-trip.
    re_up = _upload(client, ex.content)
    assert re_up.status_code == 200, re_up.text
    assert re_up.json()["skill"]["license"] == "Apache-2.0"


def test_upload_preserves_standard_metadata_and_compatibility(client):
    # A spec skill carrying metadata + compatibility must survive import → store → export.
    # The one field dropped is its allowed-tools line: a skill carries no tool list.
    md = (
        "---\n"
        "name: std-fields\n"
        "description: x\n"
        "compatibility: Designed for Open Net Worth\n"
        "metadata:\n"
        "  author: acme\n"
        '  version: "1.0"\n'
        "allowed-tools: get_transactions get_ledger_summary\n"
        "---\n\nbody\n"
    )
    up = _upload(client, _zip({"SKILL.md": md}))
    assert up.status_code == 200, up.text
    sk = up.json()["skill"]
    assert sk["compatibility"] == "Designed for Open Net Worth"
    # User metadata (non-onw-*) round-trips through the metadata map.
    assert sk["metadata"] == {"author": "acme", "version": "1.0"}
    assert "allowed_tools" not in sk
    # Export reproduces every standard field it keeps.
    ex = client.get(f"/api/skills/{sk['id']}/export")
    skill_md = _read_member(ex.content, "SKILL.md")
    assert "compatibility: Designed for Open Net Worth" in skill_md
    assert "allowed-tools" not in skill_md
    assert "author: acme" in skill_md


def test_upload_unwraps_skill_name_directory(client):
    # G1: a skill distributed as `my-skill/SKILL.md` (the canonical shape) must import.
    files = {
        "cool-skill/SKILL.md": _skill_md("cool-skill"),
        "cool-skill/references/guide.md": "# Guide\nhi",
    }
    r = _upload(client, _zip(files))
    assert r.status_code == 200, r.text
    sk = r.json()["skill"]
    assert sk["name"] == "cool-skill"
    b = client.get(f"/api/skills/{sk['id']}/bundle").json()
    assert "references/guide.md" in b["files"]  # wrapper stripped, refs preserved


def test_upload_bash_and_node_scripts_get_right_interpreter(client):
    # G4: a skill bundling .sh / .js is auto-declared with the correct interpreter (derived).
    files = {
        "SKILL.md": _skill_md("multi-lang"),
        "scripts/run.sh": "echo '{}'\n",
        "scripts/calc.js": "console.log('{}')\n",
    }
    r = _upload(client, _zip(files))
    assert r.status_code == 200, r.text
    sk = r.json()["skill"]
    assert sk["has_scripts"] is True
    b = client.get(f"/api/skills/{sk['id']}/bundle").json()
    by_id = {s["id"]: s for s in b["manifest"]["scripts"]}
    assert by_id["run"]["interpreter"] == "bash"
    assert by_id["calc"]["interpreter"] == "node"
    # Derived manifest — no manifest.json in the file tree
    assert "manifest.json" not in b["files"]


def test_upload_preserves_assets_dir(client):
    # G9: assets/ files are preserved and readable like any bundle file.
    r = _upload(
        client,
        _zip({"SKILL.md": _skill_md("with-assets"), "assets/template.txt": "hello {{name}}"}),
    )
    assert r.status_code == 200, r.text
    b = client.get(f"/api/skills/{r.json()['skill']['id']}/bundle").json()
    assert b["files"]["assets/template.txt"] == "hello {{name}}"


def test_upload_export_round_trips_binary_assets_byte_for_byte(client):
    png = b"\x89PNG\r\n\x1a\n\x00\xff\x80binary-icon"
    uploaded = _upload(
        client,
        _zip({"SKILL.md": _skill_md("binary-assets"), "assets/icon.png": png}),
    )
    assert uploaded.status_code == 200, uploaded.text
    skill_id = uploaded.json()["skill"]["id"]
    bundle = client.get(f"/api/skills/{skill_id}/bundle").json()
    assert bundle["binary_files"] == ["assets/icon.png"]

    exported = client.get(f"/api/skills/{skill_id}/export")
    assert exported.status_code == 200
    archive = zipfile.ZipFile(io.BytesIO(exported.content))
    icon_name = next(name for name in archive.namelist() if name.endswith("assets/icon.png"))
    assert archive.read(icon_name) == png


@pytest.mark.parametrize("alias", ["./SKILL.md", "agents//openai.yaml"])
def test_upload_rejects_noncanonical_zip_member_aliases(client, alias):
    files: dict[str, str] = {"SKILL.md": _skill_md("canonical-only"), alias: "shadow"}
    response = _upload(client, _zip(files))
    assert response.status_code == 400
    assert "Unsafe path" in response.text


def test_upload_rejects_malformed_invocation_controls(client):
    bad_claude = _skill_md("bad-claude").replace(
        "description: x", 'description: x\ndisable-model-invocation: "true"'
    )
    response = _upload(client, _zip({"SKILL.md": bad_claude}))
    assert response.status_code == 400
    assert "must be a boolean" in response.text

    bad_openai = "policy:\n  allow_implicit_invocation: 'false'\n"
    response = _upload(
        client,
        _zip(
            {
                "SKILL.md": _skill_md("bad-openai"),
                "agents/openai.yaml": bad_openai,
            }
        ),
    )
    assert response.status_code == 400
    assert "must be a boolean" in response.text


def test_template_is_a_zip_with_skill_md_only(client):
    """Template zip: only SKILL.md (no onw.yaml — all hints are comments in the SKILL.md)."""
    r = client.get("/api/skills/template")
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    names = zipfile.ZipFile(io.BytesIO(r.content)).namelist()
    assert _has_member(names, "SKILL.md") and not _has_member(names, "onw.yaml")
