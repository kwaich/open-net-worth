"""Tests for the extension registry (P4-EXT phase-1).

Key property: user pack > shipped > None precedence.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

from backend.services.extensions import registry as ext_registry
from backend.services.extensions.registry import (
    _domain_matches,
    get_data_pack,
    list_installed,
)

# ── Helpers ───────────────────────────────────────────────────────────────────

_VALID_MANIFEST = {
    "manifest_version": 1,
    "id": "test-pack",
    "type": "data_pack",
    "name": "Test Pack",
    "version": "1.0.0",
    "author": "Test",
    "permissions": {"reads": [], "capabilities": [], "network": False},
    "domain": {"country": "US"},
    "contributions": [{"kind": "data_pack", "engine": "sample", "path": "data/lookup.json"}],
    "scripts": [],
    "bundle_sha": "",
}


def _write_pack_dir(base_dir: Path, pack_id: str, version: str, manifest: dict) -> Path:
    """Write a pack at base_dir/<pack_id>/<version>/. Returns version_dir."""
    version_dir = base_dir / pack_id / version
    version_dir.mkdir(parents=True, exist_ok=True)
    data_dir = version_dir / "data"
    data_dir.mkdir()
    (data_dir / "lookup.json").write_text('{"key": "value"}', encoding="utf-8")
    (version_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return version_dir


def _write_shipped_pack(root: Path, engine: str, pack_id: str, manifest: dict) -> Path:
    """Write a SHIPPED pack at root/<engine>/<pack_id>/. Returns the pack dir.

    Shipped packs are flatter than user packs — no version directory — matching
    ``_resolve_shipped_pack``, which iterates ``SHIPPED_EXTENSIONS_DIR/<engine>/*``.

    Tests build their own pack rather than reading one out of the repo. The suite used to
    assert against a real `sample-data-pack` checked in under `backend/data/extensions/`,
    which meant a fixture existed in the shipped tree for no reason other than to be found by
    a test — and it put content inside `backend/data`, the legacy instance directory. Both are
    gone (2026-08-28); the loader tolerates the directory being absent entirely.
    """
    pack_dir = root / engine / pack_id
    (pack_dir / "data").mkdir(parents=True, exist_ok=True)
    (pack_dir / "data" / "lookup.json").write_text('{"key": "value"}', encoding="utf-8")
    (pack_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return pack_dir


def _compute_and_write_sha_sidecar(version_dir: Path) -> str:
    """Compute the bundle sha (excluding bundle.sha sidecar) and write the sidecar.

    Mirrors what install_extension does before promoting to USER_PACKS_DIR.
    """
    from backend.services.extensions.registry import _SHA_SIDECAR, _compute_pack_sha

    sha = _compute_pack_sha(version_dir)
    (version_dir / _SHA_SIDECAR).write_text(sha, encoding="utf-8")
    return sha


# ── Domain matching ───────────────────────────────────────────────────────────


def test_domain_matches_exact():
    from backend.models.extension import ExtensionManifest

    m = ExtensionManifest.model_validate({**_VALID_MANIFEST, "domain": {"country": "US"}})
    assert _domain_matches(m, {"country": "US"}) is True
    assert _domain_matches(m, {"country": "SG"}) is False


def test_domain_matches_year_ceiling():
    """A pack with year=2024 matches query year=2024 and year=2025
    (ceiling logic — pack_year ≤ requested)."""
    from backend.models.extension import ExtensionManifest

    m = ExtensionManifest.model_validate(
        {**_VALID_MANIFEST, "domain": {"country": "US", "year": 2024}}
    )
    assert _domain_matches(m, {"country": "US", "year": 2024}) is True
    assert _domain_matches(m, {"country": "US", "year": 2025}) is True
    # A 2026 query still matches (pack_year=2024 ≤ 2026).
    assert _domain_matches(m, {"country": "US", "year": 2026}) is True
    # 2023 query does NOT match (pack_year=2024 > 2023).
    assert _domain_matches(m, {"country": "US", "year": 2023}) is False


# ── Shipped pack resolution ───────────────────────────────────────────────────


def test_shipped_pack_resolves():
    """A pack present in the shipped tree is found by the registry for its engine."""
    manifest = {**_VALID_MANIFEST, "id": "demo-pack", "domain": {"country": "sample"}}
    with tempfile.TemporaryDirectory() as tmp:
        shipped_root = Path(tmp)
        _write_shipped_pack(shipped_root, "sample", "demo-pack", manifest)

        with patch.object(ext_registry, "SHIPPED_EXTENSIONS_DIR", shipped_root):
            result = get_data_pack("sample", {"country": "sample"})

    assert result is not None
    assert result["source"] == "shipped"
    assert result["manifest"].id == "demo-pack"
    assert result["verified"] is True


def test_shipped_pack_not_found_returns_none():
    """An unknown engine returns None (no crash)."""
    result = get_data_pack("definitely_not_an_engine_xyz", {})
    assert result is None


# ── User pack takes precedence over shipped ───────────────────────────────────


def test_user_pack_takes_precedence_over_shipped():
    """A user-installed pack MUST shadow the shipped pack for the same engine."""
    user_manifest = {
        **_VALID_MANIFEST,
        "id": "user-sample-pack",
        "domain": {"country": "sample"},
    }

    with tempfile.TemporaryDirectory(prefix="onw-test-user-packs-") as tmpdir:
        user_packs_root = Path(tmpdir)
        type_dir = user_packs_root / "data_pack"
        type_dir.mkdir()
        version_dir = _write_pack_dir(type_dir, "user-sample-pack", "1.0.0", user_manifest)
        _compute_and_write_sha_sidecar(version_dir)

        with patch.object(ext_registry, "USER_PACKS_DIR", user_packs_root):
            result = get_data_pack("sample", {"country": "sample"})

    assert result is not None, "expected a result"
    assert result["source"] == "user", f"expected user, got {result['source']!r}"
    assert result["manifest"].id == "user-sample-pack"


# ── Sha tamper detection ──────────────────────────────────────────────────────


def test_sha_mismatch_disables_user_pack():
    """A user pack whose bytes were tampered (sha mismatch) must be skipped."""
    user_manifest = {
        **_VALID_MANIFEST,
        "id": "tampered-pack",
        "domain": {"country": "sample"},
    }

    with tempfile.TemporaryDirectory(prefix="onw-test-tamper-") as tmpdir:
        user_packs_root = Path(tmpdir)
        type_dir = user_packs_root / "data_pack"
        type_dir.mkdir()
        version_dir = _write_pack_dir(type_dir, "tampered-pack", "1.0.0", user_manifest)
        # Write a WRONG sha to the sidecar (simulates post-install tampering).
        (version_dir / "bundle.sha").write_text("a" * 64, encoding="utf-8")

        with patch.object(ext_registry, "USER_PACKS_DIR", user_packs_root):
            result = get_data_pack("sample", {"country": "sample"})

    # Tampered user pack is skipped; shipped fallback may or may not match.
    # The important thing is the tampered pack is NOT returned.
    if result is not None:
        assert result["source"] != "user" or result["manifest"].id != "tampered-pack"


# ── Self-asserted verified is stripped from user packs ────────────────────────


def test_user_pack_verified_stripped():
    """A user pack that self-asserts verified=True must have it set to False by the registry."""
    user_manifest = {
        **_VALID_MANIFEST,
        "id": "self-verified-pack",
        "verified": True,
        "domain": {"country": "sample"},
    }

    with tempfile.TemporaryDirectory(prefix="onw-test-verified-") as tmpdir:
        user_packs_root = Path(tmpdir)
        type_dir = user_packs_root / "data_pack"
        type_dir.mkdir()
        version_dir = _write_pack_dir(type_dir, "self-verified-pack", "1.0.0", user_manifest)
        _compute_and_write_sha_sidecar(version_dir)

        with patch.object(ext_registry, "USER_PACKS_DIR", user_packs_root):
            result = get_data_pack("sample", {"country": "sample"})

    assert result is not None
    # verified MUST be False for user packs regardless of what the manifest says.
    assert result["verified"] is False
    assert result["manifest"].verified is False


# ── list_installed ────────────────────────────────────────────────────────────


def test_list_installed_includes_shipped():
    """list_installed surfaces packs present in the shipped tree."""
    manifest = {**_VALID_MANIFEST, "id": "demo-pack"}
    with tempfile.TemporaryDirectory() as tmp:
        shipped_root = Path(tmp)
        _write_shipped_pack(shipped_root, "sample", "demo-pack", manifest)

        with patch.object(ext_registry, "SHIPPED_EXTENSIONS_DIR", shipped_root):
            packs = list_installed()

    shipped_ids = [p["id"] for p in packs if p["source"] == "shipped"]
    assert "demo-pack" in shipped_ids


def test_list_installed_tolerates_no_shipped_tree_at_all():
    """The repo ships no packs by default, so a MISSING directory is the normal case — not an
    error, and not something a fixture should have to paper over."""
    with tempfile.TemporaryDirectory() as tmp:
        absent = Path(tmp) / "does-not-exist"
        with patch.object(ext_registry, "SHIPPED_EXTENSIONS_DIR", absent):
            packs = list_installed()
            assert [p for p in packs if p["source"] == "shipped"] == []
            assert get_data_pack("sample", {"country": "sample"}) is None


def test_list_installed_type_filter():
    """type_filter restricts the result to matching types."""
    packs = list_installed(type_filter="data_pack")
    assert all(p["type"] == "data_pack" for p in packs)

    # Asking for a type that doesn't exist returns empty (not an error).
    packs_none = list_installed(type_filter="compute_plugin")
    assert packs_none == []
