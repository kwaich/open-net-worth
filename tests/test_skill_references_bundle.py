"""References bundles (optional multi-file skills) + progressive disclosure.

Covers: references-only bundles validate + write/read; the bundle endpoint serves ANY skill
with a bundle (prompt skills too); managed skill-creator installation attaches reference files
idempotently; read_bundle_file is jailed; the read_skill_file tool reads from the active bundle.
Hermetic — conftest redirects DATA_DIR. Offline.

Run: .venv/bin/pytest tests/test_skill_references_bundle.py -v
"""

from __future__ import annotations

import asyncio
import json
import threading

import pytest
from fastapi.testclient import TestClient

from backend.ai.tools import _skill_scope, execute_tool
from backend.db import skill_store
from backend.db.skill_store import SkillStore
from backend.main import app
from backend.services.skill_bundles import (
    build_skill_md,
    parse_skill_md,
    read_bundle,
    read_bundle_file,
    version_dir,
    write_bundle,
)
from backend.services.skill_creator_bundle import ensure_skill_creator_installed

# ── Isolated helpers ──────────────────────────────────────────────────────────


def _make_isolated_store(tmp_path, monkeypatch, bundles_module=None):
    """Patch skill storage into tmp_path; return (tmp_path, store)."""
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    monkeypatch.setattr(skill_store, "SKILLS_DIR", skills_dir)
    monkeypatch.setattr(skill_store, "SKILLS_STATE_JSON", tmp_path / "skills_state.json")
    if bundles_module is None:
        from backend.services import skill_bundles as sb_mod

        bundles_module = sb_mod
    monkeypatch.setattr(bundles_module, "SKILLS_DIR", skills_dir)
    return tmp_path, SkillStore()


# ── References-only bundle writes and reads ───────────────────────────────────


def test_references_only_bundle_writes_and_reads(tmp_path, monkeypatch):
    from backend.services import skill_bundles as sb_mod

    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    monkeypatch.setattr(sb_mod, "SKILLS_DIR", skills_dir)
    vd, sha = write_bundle(
        "demo-skill",
        "live",
        skill_md="# Demo\nsee references",
        manifest={"scripts": [], "references": ["a.md"]},
        references={"a.md": "# A\nhello"},
    )
    assert sha
    bundle = read_bundle("demo-skill", "live")
    assert bundle["files"].get("references/a.md") == "# A\nhello"


def test_read_bundle_file_jailed(tmp_path, monkeypatch):
    from backend.services import skill_bundles as sb_mod

    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    monkeypatch.setattr(sb_mod, "SKILLS_DIR", skills_dir)
    write_bundle(
        "demo2",
        "live",
        skill_md="x",
        manifest={"scripts": []},
        references={"r.md": "content"},
    )
    vd = version_dir("demo2", "live")
    assert read_bundle_file(vd, "references/r.md") == "content"
    assert read_bundle_file(vd, "../../etc/passwd") is None  # traversal refused
    assert read_bundle_file(vd, "references/missing.md") is None


# ── managed skill-creator install: attaches references idempotently ───────────


def test_ensure_skill_creator_installed_adds_references_idempotently(tmp_path, monkeypatch):
    from backend.services import skill_bundles as sb_mod

    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    monkeypatch.setattr(skill_store, "SKILLS_DIR", skills_dir)
    monkeypatch.setattr(skill_store, "SKILLS_STATE_JSON", tmp_path / "skills_state.json")
    monkeypatch.setattr(sb_mod, "SKILLS_DIR", skills_dir)

    store = SkillStore()
    store.seed_starters()
    sk = store.find_by_id("skill-creator")
    assert sk is not None
    # In file-first layout there's no bundle_version — references live in the folder.
    assert sk.get("bundle_version") is None

    assert ensure_skill_creator_installed() is True  # first write
    # skill-creator folder now has references/ files
    refs_dir = skills_dir / "skill-creator" / "references"
    assert (refs_dir / "authoring-guide.md").exists()
    assert (refs_dir / "examples.md").exists()
    authoring_guide = (refs_dir / "authoring-guide.md").read_text(encoding="utf-8")
    assert "Skills are instructions, not tool policy" in authoring_guide
    assert "allowed-tools adds" not in authoring_guide
    assert "Lucius treats it as inert compatibility metadata" in authoring_guide
    # A references-only skill is NOT executable (no scripts/).
    sk2 = store.find_by_id("skill-creator")
    assert not sk2.get("has_scripts")

    assert ensure_skill_creator_installed() is False  # idempotent — already written


def test_activate_skill_surfaces_available_files(tmp_path, monkeypatch):
    """The host keeps bundle progressive disclosure active after accepting activate_skill."""
    import backend.core.paths as cp_mod
    from backend.ai import skill_runtime
    from backend.ai.tools.skills import activate_skill_scope
    from backend.services import skill_bundles as sb_mod

    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    monkeypatch.setattr(skill_store, "SKILLS_DIR", skills_dir)
    monkeypatch.setattr(skill_store, "SKILLS_STATE_JSON", tmp_path / "skills_state.json")
    monkeypatch.setattr(sb_mod, "SKILLS_DIR", skills_dir)
    # Patch SKILLS_DIR in core.paths so ai/tools/skills picks up the isolation.
    monkeypatch.setattr(cp_mod, "SKILLS_DIR", skills_dir)

    store = SkillStore()
    store.create({"id": "g6-skill", "name": "g6-skill", "body": "do it", "allowed_tools": []})
    skill_md = (skills_dir / "g6-skill" / "SKILL.md").read_text(encoding="utf-8")
    write_bundle(
        "g6-skill",
        "live",
        skill_md=skill_md,
        manifest={"scripts": [], "references": ["r.md"]},
        references={"r.md": "hi"},
    )
    _skill_scope.clear_scope()
    text, _ = asyncio.run(execute_tool("activate_skill", {"skill_id": "g6-skill"}))
    activation = skill_runtime.activation_from_tool_result(text)
    assert activation is not None
    # The tool result carries only an id; the host must re-fetch immediately before activation.
    skill = store.find_by_id(activation.skill_id)
    assert skill is not None
    with store.pinned_snapshot(activation.skill_id) as (fresh, pinned):
        assert fresh is not None and pinned is not None
        scope = activate_skill_scope(fresh, pinned_folder=pinned)
    assert "references/r.md" in scope["available_files"]
    read_text, _ = asyncio.run(execute_tool("read_skill_file", {"path": "references/r.md"}))
    assert json.loads(read_text)["content"] == "hi"
    _skill_scope.clear_scope()


def test_bundle_endpoint_serves_prompt_skill_with_bundle():
    """Live-data test: bundle endpoint returns 'live' version for file-first skills."""
    store = SkillStore()
    store.seed_starters()
    ensure_skill_creator_installed()
    store.update_skill("skill-creator", {"enabled": True})
    client = TestClient(app)
    r = client.get("/api/skills/skill-creator/bundle")
    assert r.status_code == 200, r.text
    body = r.json()
    assert "references/authoring-guide.md" in body["files"]


def test_ensure_skill_creator_installed_seeds_when_absent(tmp_path, monkeypatch):
    from backend.services import skill_bundles as sb_mod

    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    monkeypatch.setattr(skill_store, "SKILLS_DIR", skills_dir)
    monkeypatch.setattr(skill_store, "SKILLS_STATE_JSON", tmp_path / "skills_state.json")
    monkeypatch.setattr(sb_mod, "SKILLS_DIR", skills_dir)

    store = SkillStore()
    store.create({"name": "Some User Skill", "allowed_tools": ["list_skills"]})
    assert store.find_by_id("skill-creator") is None

    assert ensure_skill_creator_installed() is True  # created + references written
    sk = store.find_by_id("skill-creator")
    assert sk is not None and sk["author"] == "builtin"
    # In file-first layout: no bundle_version; references present in folder.
    assert sk.get("bundle_version") is None
    refs = skills_dir / "skill-creator" / "references"
    assert refs.is_dir()

    # Idempotent — a second call is a no-op (present + references current).
    assert ensure_skill_creator_installed() is False


def test_skill_creator_install_blocks_readers_until_body_and_references_are_complete(
    tmp_path, monkeypatch
):
    from backend.db.skill_store import STARTER_SKILLS
    from backend.services import skill_bundles as sb_mod

    _root, store = _make_isolated_store(tmp_path, monkeypatch, sb_mod)
    starter = next(row for row in STARTER_SKILLS if row["id"] == "skill-creator")
    store.create({**starter, "body": "STALE BODY", "author": "builtin"})
    write_started = threading.Event()
    release_write = threading.Event()
    reader_done = threading.Event()
    original_write = sb_mod._write_bundle_unlocked

    def gated_bundle_write(*args, **kwargs):
        write_started.set()
        assert release_write.wait(timeout=5)
        return original_write(*args, **kwargs)

    monkeypatch.setattr(sb_mod, "_write_bundle_unlocked", gated_bundle_write)
    installer = threading.Thread(target=ensure_skill_creator_installed)
    installer.start()
    assert write_started.wait(timeout=5)

    observed: list[tuple[dict | None, dict]] = []

    def read_installed():
        observed.append((store.find_by_id("skill-creator"), read_bundle("skill-creator", "live")))
        reader_done.set()

    reader = threading.Thread(target=read_installed)
    reader.start()
    assert not reader_done.wait(timeout=0.2)
    release_write.set()
    installer.join(timeout=5)
    reader.join(timeout=5)

    record, bundle = observed[0]
    assert record is not None and record["enabled"] is True
    assert "STALE BODY" not in record["body"]
    assert set(bundle["files"]) >= {
        "references/authoring-guide.md",
        "references/testing-and-iteration.md",
        "references/examples.md",
    }


def test_skill_creator_reference_failure_raises_and_leaves_skill_disabled(tmp_path, monkeypatch):
    from backend.db.skill_store import STARTER_SKILLS
    from backend.services import skill_bundles as sb_mod

    _root, store = _make_isolated_store(tmp_path, monkeypatch, sb_mod)
    starter = next(row for row in STARTER_SKILLS if row["id"] == "skill-creator")
    store.create({**starter, "author": "builtin", "enabled": True})
    original_write = sb_mod.write_pinned_skill_text

    def fail_reference(folder, relative, content):
        if relative == "references/testing-and-iteration.md":
            raise OSError("reference write failed")
        return original_write(folder, relative, content)

    monkeypatch.setattr(sb_mod, "write_pinned_skill_text", fail_reference)

    with pytest.raises(OSError, match="reference write failed"):
        ensure_skill_creator_installed()

    assert store.find_by_id("skill-creator")["enabled"] is False


def test_build_skill_md_emits_license_and_roundtrips():
    md = build_skill_md(
        {"name": "lic-skill", "description": "x", "license": "Apache-2.0", "body": "do it"}
    )
    assert "license: Apache-2.0" in md
    meta, body = parse_skill_md(md)
    assert meta["license"] == "Apache-2.0" and body.strip() == "do it"
    # No license → frontmatter stays pure (name + description only).
    assert "license:" not in build_skill_md({"name": "n", "description": "d", "body": "b"})


def test_write_bundle_preserves_extra_files(tmp_path, monkeypatch):
    from backend.services import skill_bundles as sb_mod

    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    monkeypatch.setattr(sb_mod, "SKILLS_DIR", skills_dir)
    # LICENSE.txt / NOTICE / nested files are written verbatim and come back via read_bundle.
    write_bundle(
        "lic-bundle",
        "live",
        skill_md="# x",
        manifest={"scripts": []},
        extra_files={"LICENSE.txt": "Apache 2.0 text", "NOTICE": "Copyright 2026"},
    )
    files = read_bundle("lic-bundle", "live")["files"]
    assert files["LICENSE.txt"] == "Apache 2.0 text"
    assert files["NOTICE"] == "Copyright 2026"


def test_read_skill_file_tool_reads_active_bundle():
    store = SkillStore()
    store.seed_starters()
    ensure_skill_creator_installed()
    store.update_skill("skill-creator", {"enabled": True})
    with store.pinned_snapshot("skill-creator") as (skill, pinned):
        assert skill is not None and pinned is not None
        from backend.ai.tools.skills import activate_skill_scope

        activate_skill_scope(skill, pinned_folder=pinned)
    try:
        text, _ = asyncio.run(execute_tool("read_skill_file", {"path": "references/examples.md"}))
        out = json.loads(text)
        assert "error" not in out, out
        assert "Subset-sum" in out["content"]
        # missing file → error with available_files list
        text2, _ = asyncio.run(execute_tool("read_skill_file", {"path": "nope.md"}))
        assert "error" in json.loads(text2)
    finally:
        _skill_scope.clear_scope()


def test_read_skill_file_refused_without_active_bundle():
    _skill_scope.clear_scope()
    text, _ = asyncio.run(execute_tool("read_skill_file", {"path": "references/x.md"}))
    assert "error" in json.loads(text)


def test_stacked_reference_skills_are_selected_by_skill_id(tmp_path, monkeypatch):
    _, store = _make_isolated_store(tmp_path, monkeypatch)
    store.create({"name": "first-skill", "description": "First", "body": "read it"})
    store.create({"name": "second-skill", "description": "Second", "body": "read it"})
    write_bundle(
        "first-skill",
        "live",
        skill_md=build_skill_md(store.find_by_id("first-skill")),
        manifest={"scripts": []},
        references={"guide.md": "first guide"},
    )
    write_bundle(
        "second-skill",
        "live",
        skill_md=build_skill_md(store.find_by_id("second-skill")),
        manifest={"scripts": []},
        references={"guide.md": "second guide"},
    )

    _skill_scope.clear_scope()
    from backend.ai.tools.skills import activate_skill_scope

    for skill_id in ("first-skill", "second-skill"):
        with store.pinned_snapshot(skill_id) as (skill, pinned):
            assert skill is not None and pinned is not None
            activate_skill_scope(skill, pinned_folder=pinned)
    try:
        first_text, _ = asyncio.run(
            execute_tool(
                "read_skill_file",
                {"skill_id": "first-skill", "path": "references/guide.md"},
            )
        )
        second_text, _ = asyncio.run(
            execute_tool(
                "read_skill_file",
                {"skill_id": "second-skill", "path": "references/guide.md"},
            )
        )
        ambiguous_text, _ = asyncio.run(
            execute_tool("read_skill_file", {"path": "references/guide.md"})
        )
    finally:
        _skill_scope.clear_scope()

    assert json.loads(first_text)["content"] == "first guide"
    assert json.loads(second_text)["content"] == "second guide"
    ambiguity = json.loads(ambiguous_text)["error"]
    assert "multiple active skill bundles" in ambiguity
    assert "first-skill" in ambiguity and "second-skill" in ambiguity
