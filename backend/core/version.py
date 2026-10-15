"""The running app's version, read from the installed distribution's metadata.

There is exactly one version string, `[project].version` in pyproject.toml, and it reaches the
process through the distribution metadata that pip/uv write at install time — so an installed
wheel, an editable checkout (`make setup` installs one) and `onw version` all agree, and a
release is a pyproject bump plus a matching git tag, nothing else to keep in sync.

The Docker image copies the source rather than installing it, so its Dockerfile bakes the same
`[project].version` into `backend/_bundled/VERSION` at build time; that file is read when there is
no distribution to ask. A checkout with neither (a bare `python3 -m backend.main`, CI's pytest
job) reports the dev sentinel instead of failing at import.

`AGENT_CONTRACTS` are the versioned surfaces a separately-updated peer relies on, published in
`/api/health`: `gateway` (portal <-> agent headers and health) and `move` (desktop <-> agent book
move). Bump one only with a change a peer on the previous number cannot talk to.
"""

from __future__ import annotations

from importlib import metadata
from pathlib import Path

DISTRIBUTION = "open-net-worth"
DEV_VERSION = "0.0.0+dev"
BAKED_VERSION_FILE = Path(__file__).resolve().parent.parent / "_bundled" / "VERSION"
AGENT_CONTRACTS = {"gateway": 1, "move": 1}


def app_version() -> str:
    """Return the installed distribution's version, else the image's baked one, else the sentinel."""
    try:
        return metadata.version(DISTRIBUTION)
    except metadata.PackageNotFoundError:
        pass
    try:
        return BAKED_VERSION_FILE.read_text().strip() or DEV_VERSION
    except OSError:
        return DEV_VERSION


APP_VERSION = app_version()
