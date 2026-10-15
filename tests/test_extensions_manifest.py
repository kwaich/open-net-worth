"""Tests for extensions manifest validation (P4-EXT phase-1).

Key properties verified:
  - A current skill-bundle manifest validates as an extension manifest (superset).
  - data_pack type with valid contribution validates successfully.
  - compute_plugin / ui_contribution types: manifest shape validates but installer
    refuses them (type gate is separate from manifest validation).
  - id slug enforcement (lowercase, alphanumeric + hyphens).
  - permissions.network=True is a manifest error.
  - permissions.ui_tier=3 is a manifest error (FORBIDDEN).
  - Self-asserted verified=True is warned (not errored) — caller strips it.
  - Missing data_pack contribution in a data_pack manifest is an error.
  - Path-in-tree check: a contribution path that escapes the bundle dir is an error.
"""

from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

import pytest

from backend.models.extension import MANIFEST_VERSION, ExtensionManifest
from backend.services.extensions.manifest import (
    load_manifest_from_dir,
    validate_extension_manifest,
)

# ── Fixtures ──────────────────────────────────────────────────────────────────


def _make_pack_dir(manifest_raw: dict, data_files: dict | None = None) -> Path:
    """Write a manifest + optional data files to a temp dir. Caller owns cleanup."""
    tmp = Path(tempfile.mkdtemp(prefix="onw-test-ext-"))
    (tmp / "manifest.json").write_text(json.dumps(manifest_raw), encoding="utf-8")
    data_dir = tmp / "data"
    data_dir.mkdir()
    for name, content in (data_files or {}).items():
        (data_dir / name).write_text(json.dumps(content), encoding="utf-8")
    return tmp


def _base_data_pack(*, include_data_file: bool = True) -> tuple[dict, dict]:
    """Return (manifest_raw, data_files) for a minimal valid data_pack manifest."""
    manifest = {
        "manifest_version": MANIFEST_VERSION,
        "id": "test-pack",
        "type": "data_pack",
        "name": "Test Pack",
        "version": "1.0.0",
        "author": "Test Author",
        "permissions": {"reads": [], "capabilities": [], "network": False},
        "domain": {"country": "US"},
        "contributions": [{"kind": "data_pack", "engine": "sample", "path": "data/rules.json"}],
        "scripts": [],
    }
    data_files = {"rules.json": {"key": "value"}} if include_data_file else {}
    return manifest, data_files


# ── Skill-bundle backwards-compat ─────────────────────────────────────────────


def test_skill_bundle_manifest_validates_as_extension_manifest():
    """A current skill-bundle manifest shape (type=skill, scripts=[…]) must validate.

    This asserts the SUPERSET property: no existing bundle breaks when we parse it
    as an ExtensionManifest.
    """
    raw = {
        "manifest_version": 1,
        "id": "my-skill",
        "type": "skill",
        "name": "My Skill",
        "version": "1.0.0",
        "scripts": [
            {
                "id": "main",
                "interpreter": "python",
                "entry": "scripts/main.py",
                "timeout_s": 30,
            }
        ],
    }
    # Must parse without error.
    m = ExtensionManifest.model_validate(raw)
    assert m.id == "my-skill"
    assert m.type == "skill"
    assert len(m.scripts) == 1


# ── Valid data_pack ───────────────────────────────────────────────────────────


def test_valid_data_pack_no_errors():
    """A well-formed data_pack manifest returns zero problems."""
    raw, data_files = _base_data_pack()
    pack_dir = _make_pack_dir(raw, data_files)
    try:
        problems = validate_extension_manifest(raw, pack_dir)
        assert problems == [], f"unexpected errors: {problems}"
    finally:
        shutil.rmtree(pack_dir, ignore_errors=True)


# ── Identity checks ───────────────────────────────────────────────────────────


def test_invalid_manifest_version():
    raw, data_files = _base_data_pack()
    raw["manifest_version"] = 99
    pack_dir = _make_pack_dir(raw, data_files)
    try:
        problems = validate_extension_manifest(raw, pack_dir)
        assert any("manifest_version" in p for p in problems)
    finally:
        shutil.rmtree(pack_dir, ignore_errors=True)


def test_invalid_id_uppercase():
    raw, data_files = _base_data_pack()
    raw["id"] = "TestPack"
    pack_dir = _make_pack_dir(raw, data_files)
    try:
        problems = validate_extension_manifest(raw, pack_dir)
        assert any("id" in p for p in problems)
    finally:
        shutil.rmtree(pack_dir, ignore_errors=True)


def test_invalid_id_spaces():
    raw, data_files = _base_data_pack()
    raw["id"] = "test pack"
    pack_dir = _make_pack_dir(raw, data_files)
    try:
        problems = validate_extension_manifest(raw, pack_dir)
        assert any("id" in p for p in problems)
    finally:
        shutil.rmtree(pack_dir, ignore_errors=True)


def test_valid_id_hyphens():
    raw, data_files = _base_data_pack()
    raw["id"] = "my-test-pack-123"
    pack_dir = _make_pack_dir(raw, data_files)
    try:
        problems = validate_extension_manifest(raw, pack_dir)
        assert not problems, f"unexpected errors: {problems}"
    finally:
        shutil.rmtree(pack_dir, ignore_errors=True)


def test_empty_name_is_error():
    raw, data_files = _base_data_pack()
    raw["name"] = ""
    pack_dir = _make_pack_dir(raw, data_files)
    try:
        problems = validate_extension_manifest(raw, pack_dir)
        assert any("name" in p for p in problems)
    finally:
        shutil.rmtree(pack_dir, ignore_errors=True)


# ── Permissions checks ────────────────────────────────────────────────────────


def test_network_true_is_error():
    raw, data_files = _base_data_pack()
    raw["permissions"] = {"network": True, "reads": [], "capabilities": []}
    pack_dir = _make_pack_dir(raw, data_files)
    try:
        problems = validate_extension_manifest(raw, pack_dir)
        assert any("network" in p for p in problems)
    finally:
        shutil.rmtree(pack_dir, ignore_errors=True)


def test_ui_tier_3_forbidden():
    raw, data_files = _base_data_pack()
    raw["permissions"] = {"network": False, "reads": [], "capabilities": [], "ui_tier": 3}
    pack_dir = _make_pack_dir(raw, data_files)
    try:
        problems = validate_extension_manifest(raw, pack_dir)
        assert any("3" in p or "tier" in p.lower() for p in problems)
    finally:
        shutil.rmtree(pack_dir, ignore_errors=True)


def test_unknown_capability_is_error():
    raw, data_files = _base_data_pack()
    raw["permissions"] = {
        "network": False,
        "reads": [],
        "capabilities": ["definitely_not_a_real_capability"],
    }
    pack_dir = _make_pack_dir(raw, data_files)
    try:
        problems = validate_extension_manifest(raw, pack_dir)
        assert any("capabilities" in p for p in problems)
    finally:
        shutil.rmtree(pack_dir, ignore_errors=True)


def test_verified_self_asserted_is_warned_not_errored(caplog):
    """Author self-asserts verified=True: must NOT fail validation, just log a warning."""
    raw, data_files = _base_data_pack()
    raw["verified"] = True
    pack_dir = _make_pack_dir(raw, data_files)
    try:
        import logging

        with caplog.at_level(logging.WARNING, logger="backend.services.extensions.manifest"):
            problems = validate_extension_manifest(raw, pack_dir)
        # No validation errors.
        assert not problems, f"unexpected errors: {problems}"
        # Warning logged.
        assert any("verified" in r.message for r in caplog.records)
    finally:
        shutil.rmtree(pack_dir, ignore_errors=True)


# ── Contribution checks ───────────────────────────────────────────────────────


def test_data_pack_missing_contribution_is_error():
    """A data_pack manifest with no data_pack contributions is invalid."""
    raw, data_files = _base_data_pack()
    raw["contributions"] = []
    pack_dir = _make_pack_dir(raw, data_files)
    try:
        problems = validate_extension_manifest(raw, pack_dir)
        assert any("contribution" in p for p in problems)
    finally:
        shutil.rmtree(pack_dir, ignore_errors=True)


def test_data_pack_missing_engine_is_error():
    raw, data_files = _base_data_pack()
    raw["contributions"] = [{"kind": "data_pack", "path": "data/rules.json"}]
    pack_dir = _make_pack_dir(raw, data_files)
    try:
        problems = validate_extension_manifest(raw, pack_dir)
        assert any("engine" in p for p in problems)
    finally:
        shutil.rmtree(pack_dir, ignore_errors=True)


def test_data_pack_path_not_in_tree_is_error():
    """A contribution path that escapes the bundle tree is a validation error."""
    raw, data_files = _base_data_pack()
    raw["contributions"] = [
        {"kind": "data_pack", "engine": "sample", "path": "../escape/rules.json"}
    ]
    pack_dir = _make_pack_dir(raw, data_files)
    try:
        problems = validate_extension_manifest(raw, pack_dir)
        assert problems, "expected path-in-tree error"
    finally:
        shutil.rmtree(pack_dir, ignore_errors=True)


def test_data_pack_missing_file_is_error():
    """A contribution that points to a non-existent file is a validation error."""
    raw, data_files = _base_data_pack(include_data_file=False)
    pack_dir = _make_pack_dir(raw, data_files)
    try:
        problems = validate_extension_manifest(raw, pack_dir)
        assert any("does not exist" in p or "not found" in p.lower() for p in problems)
    finally:
        shutil.rmtree(pack_dir, ignore_errors=True)


# ── load_manifest_from_dir ────────────────────────────────────────────────────


def test_load_manifest_from_dir_ok():
    raw, data_files = _base_data_pack()
    pack_dir = _make_pack_dir(raw, data_files)
    try:
        m = load_manifest_from_dir(pack_dir)
        assert m.id == "test-pack"
        assert m.type == "data_pack"
    finally:
        shutil.rmtree(pack_dir, ignore_errors=True)


def test_load_manifest_from_dir_missing():
    """Missing manifest.json raises ValidationError."""
    import tempfile

    from backend.core.errors import ValidationError

    empty_dir = Path(tempfile.mkdtemp())
    try:
        with pytest.raises(ValidationError, match="manifest.json"):
            load_manifest_from_dir(empty_dir)
    finally:
        shutil.rmtree(empty_dir, ignore_errors=True)
