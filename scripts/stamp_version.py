"""Stamp one release version into every file that carries it.

The desktop runtime manifest refuses a build whose desktop/package.json and pyproject.toml
disagree, so a release sets both (plus the lockfile's own copy) in one step, and the compose
file's default image tag so the file shipped with a release pulls that release:

    python3 scripts/stamp_version.py 0.1.2

`make release-commit VERSION=x.y.z` runs this and commits the result; tests/test_release_versions.py
fails if any of the stamped copies drifts from pyproject.toml.
"""

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SEMVER = re.compile(r"^\d+\.\d+\.\d+$")


def _stamp_json(path: Path, version: str, lockfile: bool) -> None:
    data = json.loads(path.read_text())
    data["version"] = version
    if lockfile:
        data["packages"][""]["version"] = version
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def _stamp_pyproject(path: Path, version: str) -> None:
    text = path.read_text()
    stamped, count = re.subn(r'(?m)^version = "[^"]*"$', f'version = "{version}"', text, count=1)
    if count != 1:
        raise SystemExit(f"no top-level version line in {path}")
    path.write_text(stamped)


def _stamp_compose(path: Path, version: str) -> None:
    text = path.read_text()
    stamped, count = re.subn(r"(\$\{ONW_VERSION:-)[^}]*\}", rf"\g<1>{version}}}", text, count=1)
    if count != 1:
        raise SystemExit(f"no ONW_VERSION default in {path}")
    path.write_text(stamped)


def main(argv: list[str]) -> None:
    if len(argv) != 2 or not SEMVER.match(argv[1]):
        raise SystemExit("usage: stamp_version.py X.Y.Z")
    version = argv[1]
    _stamp_json(ROOT / "desktop" / "package.json", version, lockfile=False)
    _stamp_json(ROOT / "desktop" / "package-lock.json", version, lockfile=True)
    _stamp_pyproject(ROOT / "pyproject.toml", version)
    _stamp_compose(ROOT / "docker-compose.yml", version)
    sys.stdout.write(f"stamped {version}\n")


if __name__ == "__main__":
    main(sys.argv)
