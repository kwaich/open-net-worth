"""
Unit tests for skill bundles + script-skill fields (file-first layout).

Pins the folder-first storage contract:
  - a plain skill round-trips with no has_scripts / enabled_script fields;
  - a script skill derives has_scripts live and has no removed per-skill gate fields;
  - write_bundle / read_bundle round-trip (mutable in-place, no version dirs);
  - bundle_sha computed over scripts/; tamper detected (sha mismatch);
  - in-place re-write updates the SKILL.md (no immutability in file-first layout);
  - path-traversal in script entries rejected;
  - manifest validation (good shape, bad interpreter, out-of-tree, network-on);
  - skills_service script-validate branch.

Pure, offline, fully isolated into tmp_path. No LLM, no network.

Run:
    .venv/bin/pytest tests/test_skill_bundles.py -v
"""

from __future__ import annotations

import pytest

from backend.core.errors import NotFoundError, ValidationError
from backend.db import skill_store
from backend.db.skill_store import SkillStore
from backend.services import skill_bundles


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    """Isolate both stores: skills folder and state into tmp_path."""
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    monkeypatch.setattr(skill_store, "SKILLS_DIR", skills_dir)
    monkeypatch.setattr(skill_store, "SKILLS_STATE_JSON", tmp_path / "skills_state.json")
    monkeypatch.setattr(skill_bundles, "SKILLS_DIR", skills_dir)
    return tmp_path


def _manifest(entry: str = "scripts/normalize.py") -> dict:
    return {
        "scripts": [
            {
                "id": "normalize",
                "interpreter": "python",
                "entry": entry,
                "description": "Normalize a pasted CSV",
                "timeout_s": 30,
                "network": False,
                "network_allow": [],
            }
        ]
    }


# ── plain skill has no script fields (back-compat by absence) ─────────────────


def test_prompt_skill_has_no_script_fields(isolated):
    s = SkillStore()
    sk = s.create({"name": "Monthly Close", "allowed_tools": ["get_ledger_summary"]})
    # No scripts/ dir → none of the script-gate fields are stamped.
    assert "has_scripts" not in sk
    assert "enabled_script" not in sk


def test_explicit_prompt_kind_is_dropped_to_today_shape(isolated):
    s = SkillStore()
    sk = s.create({"name": "X", "allowed_tools": ["x"], "kind": "prompt"})
    # kind is not a recognised field — not stamped.
    assert "has_scripts" not in sk and "enabled_script" not in sk


def test_bad_kind_coerces_to_prompt(isolated):
    s = SkillStore()
    sk = s.create({"name": "X", "allowed_tools": ["x"], "kind": "shell-exec"})
    assert "has_scripts" not in sk


# ── script skill: has_scripts derived live from scripts/ presence ─────────────


def _make_script_skill(store, name: str, scripts_dir_path) -> dict:
    """Helper: create a skill and write an actual scripts/ directory so has_scripts is True."""
    sk = store.create({"name": name, "allowed_tools": ["x"]})
    scripts_dir = scripts_dir_path / "skills" / sk["id"] / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    (scripts_dir / "run.py").write_text("print(1)\n")
    # Re-read to get the live has_scripts derived state
    return store.find_by_id(sk["id"])


def test_script_skill_has_scripts_derived_no_per_skill_gates(isolated):
    s = SkillStore()
    sk = _make_script_skill(s, "CSV Normalizer", isolated)
    # has_scripts is derived from scripts/ presence.
    assert sk["has_scripts"] is True
    # No per-skill gate fields — permissions are instance-level (Settings → Developer).
    assert "enabled_script" not in sk
    assert "allow_unattended" not in sk
    # Survives a reload from disk.
    again = s.find_by_id(sk["id"])
    assert again["has_scripts"] is True
    assert "enabled_script" not in again


def test_legacy_enabled_script_in_update_is_silently_ignored(isolated):
    """Passing enabled_script in update_skill is accepted (back-compat) but has no effect."""
    s = SkillStore()
    sk = _make_script_skill(s, "X", isolated)
    updated = s.update_skill(sk["id"], {"enabled_script": True})
    # No per-skill gate field on the record.
    assert updated is not None
    assert "enabled_script" not in updated


def test_update_skill_unknown_keys_silently_dropped(isolated):
    """Unknown keys in changes (e.g. bundle_version) are silently dropped — not 422."""
    s = SkillStore()
    sk = s.create({"name": "X", "allowed_tools": ["x"]})
    updated = s.update_skill(sk["id"], {"bundle_version": "", "unknown_field": "x"})
    assert updated is not None  # update succeeded
    assert updated.get("bundle_version") is None
    assert "unknown_field" not in updated


# ── bundle write/read (mutable in-place, no version dirs) ────────────────────


def test_write_and_read_bundle(isolated):
    vd, sha = skill_bundles.write_bundle(
        "csv-normalizer",
        "live",
        skill_md="# CSV Normalizer\nRun normalize on pasted CSV.",
        manifest=_manifest(),
        scripts={"normalize.py": "import sys\nprint('{}')\n"},
        resources={"lookup.json": '{"a": 1}'},
    )
    assert vd.is_dir()
    assert sha and len(sha) == 64  # sha256 hex
    bundle = skill_bundles.read_bundle("csv-normalizer", "live")
    assert bundle["skill_md"].startswith("# CSV Normalizer")
    # Manifest is derived from scripts/ — id = filename stem
    assert bundle["manifest"]["scripts"][0]["id"] == "normalize"
    assert bundle["manifest"]["scripts"][0]["interpreter"] == "python"
    assert bundle["manifest"]["scripts"][0]["entry"] == "scripts/normalize.py"
    assert bundle["files"]["scripts/normalize.py"].startswith("import sys")
    assert bundle["files"]["resources/lookup.json"] == '{"a": 1}'
    # SKILL.md and manifest.json are NOT in files.
    assert "SKILL.md" not in bundle["files"]
    assert "manifest.json" not in bundle["files"]
    assert skill_bundles.list_versions("csv-normalizer") == ["live"]


def test_read_missing_bundle_raises_not_found(isolated):
    with pytest.raises(NotFoundError):
        skill_bundles.read_bundle("ghost", "live")


# ── bundle_sha computes, verifies, detects tampering ──────────────────────────


def test_bundle_sha_verifies(isolated):
    _, sha = skill_bundles.write_bundle(
        "x",
        "live",
        skill_md="hi",
        manifest=_manifest(),
        scripts={"normalize.py": "print(1)\n"},
    )
    assert skill_bundles.verify_bundle_sha("x", "live", sha) is True


def test_bundle_sha_detects_tamper(isolated):
    _, sha = skill_bundles.write_bundle(
        "x",
        "live",
        skill_md="hi",
        manifest=_manifest(),
        scripts={"normalize.py": "print(1)\n"},
    )
    # Tamper with a script byte on disk OUT OF BAND (mirrors T7 — edit after enable).
    script = skill_bundles.version_dir("x", "live") / "scripts" / "normalize.py"
    script.write_text("print(666)  # injected\n", encoding="utf-8")
    assert skill_bundles.verify_bundle_sha("x", "live", sha) is False
    # The freshly recomputed sha differs from the pinned one.
    assert skill_bundles.compute_bundle_sha(skill_bundles.version_dir("x", "live")) != sha


def test_verify_missing_version_is_false(isolated):
    assert skill_bundles.verify_bundle_sha("x", "live", "deadbeef") is False


# ── version_dir(): returns the skill folder (version concept is gone) ─────────
# The new layout keeps version_dir() for back-compat but always returns the folder.
# Only empty version strings are refused; any non-empty string is accepted.


def test_version_dir_empty_rejected(isolated):
    """version_dir() refuses an empty version string."""
    with pytest.raises(ValidationError):
        skill_bundles.version_dir("my-skill", "")


def test_version_dir_plain_name_accepted(isolated):
    """version_dir() accepts a plain version name."""
    vd = skill_bundles.version_dir("my-skill", "v1")
    # Returns the skill folder (not a versioned subdir).
    assert vd.name == "my-skill"


def test_version_dir_any_nonempty_accepted(isolated):
    """In the file-first layout any non-empty version string is accepted; version is ignored."""
    for v in ("live", "v1", "v2", "latest", "v1/subpath"):
        vd = skill_bundles.version_dir("my-skill", v)
        assert vd.name == "my-skill"


# ── in-place re-write (no immutability in file-first layout) ──────────────────


def test_write_bundle_is_mutable_in_place(isolated):
    """write_bundle() may overwrite an existing skill folder (mutable, no version lock)."""
    skill_bundles.write_bundle(
        "x", "live", skill_md="a", manifest=_manifest(), scripts={"normalize.py": "print(1)\n"}
    )
    # Second write updates the content.
    vd, sha2 = skill_bundles.write_bundle(
        "x", "live", skill_md="b", manifest=_manifest(), scripts={"normalize.py": "print(2)\n"}
    )
    bundle = skill_bundles.read_bundle("x", "live")
    assert bundle["skill_md"].strip() == "b"


# ── path-traversal in scripts rejected ────────────────────────────────────────


def test_path_traversal_script_rejected(isolated):
    with pytest.raises((ValidationError, Exception)):
        skill_bundles.write_bundle(
            "x",
            "live",
            skill_md="a",
            manifest=_manifest(entry="scripts/normalize.py"),
            scripts={"../escape.py": "print(1)\n"},
        )


@pytest.mark.parametrize("skill_id", ["../escape", "nested/skill", "UPPER", "bad_slug"])
def test_every_bundle_lookup_rejects_noncanonical_skill_ids(isolated, skill_id):
    for lookup in (
        lambda: skill_bundles.version_dir(skill_id, "live"),
        lambda: skill_bundles.list_versions(skill_id),
        lambda: skill_bundles.read_bundle(skill_id, "live"),
        lambda: skill_bundles.verify_bundle_sha(skill_id, "live", "0" * 64),
    ):
        with pytest.raises(ValidationError, match="canonical slug"):
            lookup()


def test_bundle_lookup_rejects_symlinked_skill_root(isolated):
    outside = isolated / "outside-bundle"
    outside.mkdir()
    (outside / "SKILL.md").write_text("body")
    (isolated / "skills" / "escaped").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValidationError, match="symlink"):
        skill_bundles.read_bundle("escaped", "live")


def test_bundle_rejects_symlink_alias_to_another_skill_inside_store(isolated):
    victim = isolated / "skills" / "victim"
    victim.mkdir()
    (victim / "SKILL.md").write_text("victim")
    (isolated / "skills" / "alias").symlink_to(victim, target_is_directory=True)

    calls = (
        lambda: skill_bundles.version_dir("alias", "live"),
        lambda: skill_bundles.list_versions("alias"),
        lambda: skill_bundles.read_bundle("alias", "live"),
        lambda: skill_bundles.verify_bundle_sha("alias", "live", "0" * 64),
        lambda: skill_bundles.write_bundle(
            "alias", "live", skill_md="overwritten", manifest={"scripts": []}
        ),
    )
    for call in calls:
        with pytest.raises(ValidationError, match="symlink"):
            call()

    assert (victim / "SKILL.md").read_text() == "victim"


def test_bundle_write_rejects_symlinked_skill_md(isolated):
    outside = isolated / "outside.md"
    outside.write_text("do not overwrite")
    folder = isolated / "skills" / "escaped"
    folder.mkdir()
    (folder / "SKILL.md").symlink_to(outside)

    with pytest.raises(ValidationError, match="regular file"):
        skill_bundles.write_bundle(
            "escaped",
            "live",
            skill_md="replacement",
            manifest={"scripts": []},
        )

    assert outside.read_text() == "do not overwrite"


def test_bundle_write_folder_swap_after_disable_never_overwrites_victim(isolated, monkeypatch):
    store = SkillStore()
    skill = store.create({"name": "bundle-swap", "body": "original", "enabled": True})
    lexical = isolated / "skills" / skill["id"]
    moved_original = isolated / "moved-bundle-original"
    victim = isolated / "bundle-victim"
    victim.mkdir()
    (victim / "SKILL.md").write_text("DO NOT OVERWRITE", encoding="utf-8")
    original_save = skill_bundles._save_state
    swapped = False

    def save_then_swap(state):
        nonlocal swapped
        original_save(state)
        if not swapped and state[skill["id"]]["enabled"] is False:
            lexical.rename(moved_original)
            victim.rename(lexical)
            swapped = True

    monkeypatch.setattr(skill_bundles, "_save_state", save_then_swap)

    with pytest.raises(ValidationError, match="identity changed"):
        skill_bundles.write_bundle(
            skill["id"],
            "live",
            skill_md="replacement",
            manifest={"scripts": []},
            references={"guide.md": "new reference"},
        )

    assert (lexical / "SKILL.md").read_text() == "DO NOT OVERWRITE"
    assert "original" in (moved_original / "SKILL.md").read_text()
    # skills_state is DB-backed now (SkillStateStore) — save_then_swap's original_save(state)
    # call above already routed the real _save_state through to it; read it back the same way.
    from backend.db.skill_store import _load_state

    state = _load_state()
    assert state[skill["id"]]["enabled"] is False


# ── manifest validation ───────────────────────────────────────────────────────


def test_validate_manifest_ok(isolated):
    vd, _ = skill_bundles.write_bundle(
        "x", "live", skill_md="a", manifest=_manifest(), scripts={"normalize.py": "print(1)\n"}
    )
    assert skill_bundles.validate_manifest(_manifest(), vd) == []


def test_validate_manifest_rejects_bad_interpreter(tmp_path):
    bad = _manifest()
    bad["scripts"][0]["interpreter"] = "ruby"  # python/bash/node are valid; ruby is not
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "normalize.py").write_text("x")
    problems = skill_bundles.validate_manifest(bad, tmp_path)
    assert any("unsupported interpreter" in p for p in problems)


def test_validate_manifest_rejects_out_of_tree_entry(tmp_path):
    bad = _manifest(entry="../outside.py")
    problems = skill_bundles.validate_manifest(bad, tmp_path)
    assert any("outside" in p for p in problems)


def test_validate_manifest_rejects_missing_entry_file(tmp_path):
    problems = skill_bundles.validate_manifest(_manifest(), tmp_path)
    assert any("does not exist" in p for p in problems)


def test_validate_manifest_rejects_network_on(tmp_path):
    bad = _manifest()
    bad["scripts"][0]["network"] = True
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "normalize.py").write_text("x")
    problems = skill_bundles.validate_manifest(bad, tmp_path)
    assert any("network is OFF" in p for p in problems)


def test_validate_manifest_allows_empty_scripts_references_only(tmp_path):
    assert skill_bundles.validate_manifest({"scripts": []}, tmp_path) == []
    assert skill_bundles.validate_manifest({}, tmp_path) == []


def test_write_bundle_ignores_caller_manifest_interpreter(isolated):
    """The caller's manifest is no longer used; the derived manifest drives validation.
    A caller passing interpreter='ruby' does NOT cause rejection — the derived manifest
    uses interpreter_for() which maps .py → python."""
    bad = _manifest()
    bad["scripts"][0]["interpreter"] = "ruby"
    # No exception — derived manifest overrides the caller-supplied one
    vd, _ = skill_bundles.write_bundle(
        "x", "live", skill_md="a", manifest=bad, scripts={"normalize.py": "print(1)\n"}
    )
    bundle = skill_bundles.read_bundle("x", "live")
    # Derived from scripts/normalize.py → interpreter is python, not ruby
    assert bundle["manifest"]["scripts"][0]["interpreter"] == "python"


def test_read_bundle_ignores_corrupt_manifest_json(isolated):
    """A corrupt on-disk manifest.json is silently ignored — manifest is derived from scripts/."""
    vd = skill_bundles.version_dir("x", "live")
    vd.mkdir(parents=True, exist_ok=True)
    (vd / "SKILL.md").write_text("---\nname: x\ndescription: x\n---\nbody\n", encoding="utf-8")
    (vd / "manifest.json").write_text("{not json", encoding="utf-8")
    # Must NOT raise; manifest.json is ignored; derived manifest has no scripts
    bundle = skill_bundles.read_bundle("x", "live")
    assert bundle["manifest"] == {"scripts": []}
    # manifest.json must not appear in the returned files dict
    assert "manifest.json" not in bundle["files"]


def test_read_bundle_excludes_manifest_json_from_files(isolated):
    """manifest.json on disk is excluded from the returned files dict even if present."""
    vd = skill_bundles.version_dir("x", "live")
    vd.mkdir(parents=True, exist_ok=True)
    (vd / "SKILL.md").write_text("---\nname: x\ndescription: x\n---\nbody\n", encoding="utf-8")
    (vd / "manifest.json").write_text('{"scripts": []}', encoding="utf-8")
    bundle = skill_bundles.read_bundle("x", "live")
    assert "manifest.json" not in bundle["files"]


def test_read_bundle_derives_manifest_from_scripts_folder(isolated):
    """Manifest is derived from scripts/ even when manifest.json is absent."""
    skill_bundles.write_bundle(
        "x",
        "live",
        skill_md="---\nname: x\ndescription: x\n---\nbody\n",
        manifest={},
        scripts={"analyze.py": "print(1)\n"},
    )
    bundle = skill_bundles.read_bundle("x", "live")
    scripts = bundle["manifest"]["scripts"]
    assert len(scripts) == 1
    assert scripts[0]["id"] == "analyze"
    assert scripts[0]["interpreter"] == "python"
    assert scripts[0]["entry"] == "scripts/analyze.py"


# ── skills_service script-validate branch (NO execution) ──────────────────────


def test_service_validate_prompt_skill_unchanged(isolated, monkeypatch):
    from backend.services import skills as svc

    monkeypatch.setattr(svc, "_registered_tool_names", lambda: {"x"})
    # A plain skill never enters the script branch.
    assert svc.validate({"allowed_tools": ["x"]}) == []


def test_service_validate_script_skill_ok(isolated, monkeypatch):
    from backend.services import skills as svc

    monkeypatch.setattr(svc, "_registered_tool_names", lambda: {"x"})
    _, sha = skill_bundles.write_bundle(
        "csv-normalizer",
        "live",
        skill_md="---\nname: csv-normalizer\ndescription: test\n---\nbody\n",
        manifest=_manifest(),
        scripts={"normalize.py": "print(1)\n"},
    )
    skill = {
        "id": "csv-normalizer",
        "allowed_tools": ["x"],
        "has_scripts": True,
        "scripts_sha": sha,
    }
    assert svc.validate(skill) == []


def test_service_validate_script_skill_missing_bundle(isolated, monkeypatch):
    from backend.services import skills as svc

    monkeypatch.setattr(svc, "_registered_tool_names", lambda: {"x"})
    skill = {"id": "ghost", "allowed_tools": ["x"], "has_scripts": True}
    problems = svc.validate(skill)
    assert any("not found on disk" in p for p in problems)


def test_service_validate_script_skill_no_sha_check(isolated, monkeypatch):
    """Validate no longer checks scripts_sha — sha pinning was a per-skill gate,
    now removed. A tampered bundle passes validation (the trust model is the sandbox
    isolation + instance kill switches, not a per-skill sha pin)."""
    from backend.services import skills as svc

    monkeypatch.setattr(svc, "_registered_tool_names", lambda: {"x"})
    _, sha = skill_bundles.write_bundle(
        "csv-normalizer",
        "live",
        skill_md="---\nname: csv-normalizer\ndescription: test\n---\nbody\n",
        manifest=_manifest(),
        scripts={"normalize.py": "print(1)\n"},
    )
    # Tamper after the fact.
    (skill_bundles.version_dir("csv-normalizer", "live") / "scripts" / "normalize.py").write_text(
        "print('evil')\n", encoding="utf-8"
    )
    # Even with an old sha on the skill dict, validate() no longer checks it.
    skill = {
        "id": "csv-normalizer",
        "allowed_tools": ["x"],
        "has_scripts": True,
        "scripts_sha": sha,
    }
    problems = svc.validate(skill)
    # Manifest validation passes (entry file exists, interpreter valid, etc.).
    assert not any("integrity check" in p for p in problems)

    # The sha utilities still work for callers who want them (run_sandboxed with sha pin).
    assert skill_bundles.verify_bundle_sha("csv-normalizer", "live", sha) is False
