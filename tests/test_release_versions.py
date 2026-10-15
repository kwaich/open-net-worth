"""One release version: pyproject.toml is the source, every stamped copy must agree.

`scripts/stamp_version.py` (run by `make release-commit`) writes the copies; this fails the
moment one is edited by hand or a release commit forgets one. The desktop runtime manifest
refuses a build whose desktop and backend versions differ, the compose file pins the image the
release publishes, and release.yml re-checks the tag against all of them.
"""

from __future__ import annotations

import json
import re
import shutil
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _pyproject_version() -> str:
    return tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]


def test_desktop_package_and_lockfile_match_pyproject():
    version = _pyproject_version()
    package = json.loads((ROOT / "desktop" / "package.json").read_text())
    lock = json.loads((ROOT / "desktop" / "package-lock.json").read_text())
    assert package["version"] == version
    assert lock["version"] == version
    assert lock["packages"][""]["version"] == version


def test_compose_default_image_tag_is_the_release_version():
    compose = (ROOT / "docker-compose.yml").read_text()
    assert (
        re.search(r"open-net-worth:\$\{ONW_VERSION:-([^}]*)\}", compose).group(1)
        == _pyproject_version()
    )


def test_stamp_version_rewrites_every_copy(tmp_path, monkeypatch):
    sys.path.insert(0, str(ROOT / "scripts"))
    try:
        import stamp_version
    finally:
        sys.path.remove(str(ROOT / "scripts"))
    for rel in (
        "pyproject.toml",
        "docker-compose.yml",
        "desktop/package.json",
        "desktop/package-lock.json",
    ):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(ROOT / rel, tmp_path / rel)
    monkeypatch.setattr(stamp_version, "ROOT", tmp_path)
    stamp_version.main(["stamp_version.py", "9.8.7"])
    assert tomllib.loads((tmp_path / "pyproject.toml").read_text())["project"]["version"] == "9.8.7"
    assert json.loads((tmp_path / "desktop/package.json").read_text())["version"] == "9.8.7"
    lock = json.loads((tmp_path / "desktop/package-lock.json").read_text())
    assert lock["version"] == lock["packages"][""]["version"] == "9.8.7"
    assert "${ONW_VERSION:-9.8.7}" in (tmp_path / "docker-compose.yml").read_text()
