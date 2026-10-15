#!/usr/bin/env python3
"""Copy the built, non-Python assets the app serves into backend/_bundled/ so the wheel ships them.

Run AFTER `npm run build` (frontend/) and `npm run build:embed` (docs-site/), BEFORE `uv build`.
`make wheel` does all of it in order. The destination is gitignored and rebuilt from scratch on
every run, so nothing stale survives. backend/core/shipped_assets.py resolves each directory at
runtime — repo copy first, bundled copy only inside an installed wheel.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BUNDLED_DIR = REPO_ROOT / "backend" / "_bundled"

# relative directory -> a file that proves it was actually built (None = any markdown file)
ASSETS: dict[str, str | None] = {
    "frontend/dist": "index.html",
    "docs-site/dist-embed": "index.html",
    "docs/product": None,
}


def _is_built(relative: str, marker: str | None) -> bool:
    source = REPO_ROOT / relative
    if marker is not None:
        return (source / marker).is_file()
    return source.is_dir() and any(source.rglob("*.md"))


def _dir_size(path: Path) -> int:
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())


def main() -> int:
    missing = [rel for rel, marker in ASSETS.items() if not _is_built(rel, marker)]
    if missing:
        print("ERROR: these assets are not built yet:", file=sys.stderr)
        for rel in missing:
            print(f"  - {rel}", file=sys.stderr)
        print(
            "Build them first: (cd frontend && npm run build) and"
            " (cd docs-site && npm run build:embed) — or just run `make wheel`.",
            file=sys.stderr,
        )
        return 1

    if BUNDLED_DIR.exists():
        shutil.rmtree(BUNDLED_DIR)
    for rel in ASSETS:
        shutil.copytree(
            REPO_ROOT / rel,
            BUNDLED_DIR / rel,
            ignore=shutil.ignore_patterns(".DS_Store", "*.map"),
        )
        print(f"==> bundled {rel} ({_dir_size(BUNDLED_DIR / rel) / 1_000_000:.1f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
