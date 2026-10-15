"""scripts/release_sign.sh — signs with the owner's key and refuses any other key."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
pytestmark = pytest.mark.skipif(
    shutil.which("minisign") is None or shutil.which("bash") is None,
    reason="needs minisign and bash",
)


def _keypair(directory: Path, name: str) -> tuple[str, str]:
    pub, key = directory / f"{name}.pub", directory / f"{name}.key"
    subprocess.run(
        ["minisign", "-G", "-W", "-f", "-p", str(pub), "-s", str(key)],
        check=True,
        capture_output=True,
    )
    return pub.read_text().splitlines()[1], key.read_text()


@pytest.fixture
def tree(tmp_path: Path):
    """A throwaway checkout holding the script, an embedded public key and a release dir."""
    (tmp_path / "scripts").mkdir()
    (tmp_path / "desktop/src/main").mkdir(parents=True)
    (tmp_path / "rel").mkdir()
    shutil.copy(ROOT / "scripts/release_sign.sh", tmp_path / "scripts/release_sign.sh")
    (tmp_path / "rel/SHA256SUMS").write_text("sums\n")
    (tmp_path / "rel/manifest.json").write_text("{}\n")
    public, secret = _keypair(tmp_path, "owner")
    (tmp_path / "desktop/src/main/update-key.ts").write_text(
        f"export const UPDATE_PUBLIC_KEY = '{public}';\n"
    )
    return tmp_path, secret


def _sign(root: Path, secret: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "ONW_MINISIGN_SECRET_KEY": secret}
    return subprocess.run(
        ["bash", str(root / "scripts/release_sign.sh"), "0.2.0", str(root / "rel")],
        env=env,
        capture_output=True,
        text=True,
    )


def test_signs_sums_and_manifest_with_the_embedded_key(tree):
    root, secret = tree
    result = _sign(root, secret)
    assert result.returncode == 0, result.stdout + result.stderr
    assert (root / "rel/SHA256SUMS.minisig").is_file()
    assert (root / "rel/manifest.json.minisig").is_file()


def test_a_missing_secret_fails_loudly(tree):
    root, _ = tree
    result = _sign(root, "")
    assert result.returncode != 0
    assert "ONW_MINISIGN_SECRET_KEY is not set" in result.stdout


def test_a_key_that_is_not_the_embedded_one_fails(tree):
    root, _ = tree
    _, other_secret = _keypair(root, "other")
    result = _sign(root, other_secret)
    assert result.returncode != 0
    assert "does not verify against the public key embedded in the app" in result.stdout
