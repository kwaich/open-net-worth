"""scripts/release_manifest.py — the signed manifest the portal rolls agents from."""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location(
    "release_manifest", ROOT / "scripts" / "release_manifest.py"
)
rm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rm)

VERSION = "0.2.0"
COMMIT = "a" * 40
DIGEST = "sha256:" + "b" * 64


def _stage(directory: Path, version: str = VERSION) -> None:
    names = [
        *rm.required_desktop_assets(version),
        *rm.SERVER_ASSETS,
        f"open_net_worth-{version}-py3-none-any.whl",
        f"open_net_worth-{version}.tar.gz",
    ]
    for name in names:
        (directory / name).write_text(f"synthetic {name}")


def _generate(directory: Path) -> dict:
    rm.generate(directory, VERSION, COMMIT, DIGEST)
    return json.loads((directory / rm.MANIFEST).read_text())


def test_manifest_records_version_commit_image_and_contracts(tmp_path):
    _stage(tmp_path)
    manifest = _generate(tmp_path)
    assert manifest["version"] == VERSION
    assert manifest["source_commit"] == COMMIT
    assert manifest["image"] == {
        "repo": "ghcr.io/open-net-worth-ai/open-net-worth",
        "digest": DIGEST,
        "tags": [VERSION],
    }
    assert manifest["contracts"] == {"gateway": 1, "move": 1, "handoff": 1}
    assert manifest["min_portal_contracts"] == {"gateway": 1, "handoff": 1}


def test_every_asset_carries_its_real_sha256(tmp_path):
    _stage(tmp_path)
    manifest = _generate(tmp_path)
    dmg = next(a for a in manifest["desktop"]["assets"] if a["name"].endswith(".dmg"))
    assert dmg["sha256"] == hashlib.sha256(b"synthetic Open-Net-Worth-mac-arm64.dmg").hexdigest()
    assert manifest["python"]["wheel"]["name"] == f"open_net_worth-{VERSION}-py3-none-any.whl"
    assert [a["name"] for a in manifest["server"]["assets"]] == list(rm.SERVER_ASSETS)


def test_sums_cover_every_file_including_the_manifest_but_not_themselves(tmp_path):
    _stage(tmp_path)
    _generate(tmp_path)
    names = [line.split("  ", 1)[1] for line in (tmp_path / rm.SUMS).read_text().splitlines()]
    assert rm.MANIFEST in names
    assert rm.SUMS not in names
    assert names == sorted(names)


def test_generation_is_deterministic(tmp_path):
    _stage(tmp_path)
    first = _generate(tmp_path), (tmp_path / rm.SUMS).read_text()
    assert (_generate(tmp_path), (tmp_path / rm.SUMS).read_text()) == first


def test_check_passes_then_catches_a_swapped_asset(tmp_path):
    _stage(tmp_path)
    _generate(tmp_path)
    assert rm.check(tmp_path) == []
    (tmp_path / "RELEASES").write_text("tampered")
    problems = rm.check(tmp_path)
    assert any("RELEASES does not match the manifest" in p for p in problems)
    assert any("RELEASES does not match SHA256SUMS" in p for p in problems)


def test_check_catches_a_missing_asset(tmp_path):
    _stage(tmp_path)
    _generate(tmp_path)
    (tmp_path / "install.ps1").unlink()
    assert any("install.ps1" in p for p in rm.check(tmp_path))


def test_a_missing_platform_refuses_to_generate(tmp_path):
    _stage(tmp_path)
    (tmp_path / "Open-Net-Worth-windows-x64-setup.exe").unlink()
    with pytest.raises(SystemExit, match="windows-x64-setup.exe"):
        rm.generate(tmp_path, VERSION, COMMIT, DIGEST)


def test_a_wheel_of_another_version_refuses_to_generate(tmp_path):
    _stage(tmp_path)
    (tmp_path / f"open_net_worth-{VERSION}-py3-none-any.whl").rename(
        tmp_path / "open_net_worth-0.1.9-py3-none-any.whl"
    )
    with pytest.raises(SystemExit, match="does not carry version"):
        rm.generate(tmp_path, VERSION, COMMIT, DIGEST)


@pytest.mark.parametrize(
    "version,commit,digest",
    [("0.2", COMMIT, DIGEST), (VERSION, "abc123", DIGEST), (VERSION, COMMIT, "latest")],
)
def test_malformed_inputs_are_refused(tmp_path, version, commit, digest):
    _stage(tmp_path)
    with pytest.raises(SystemExit):
        rm.generate(tmp_path, version, commit, digest)
