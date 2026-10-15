#!/usr/bin/env python3
"""List every place the OLD logo still appears, across the app, landing page and account site.

Usage: python3 scripts/brand_leftovers.py OLD_LOGO.svg [repo ...]
Fingerprints the old SVG (its <title> and the start of each path's `d` data) and searches every
tracked text file in the repos for them; also lists brand image files for a manual look.
"""

import re
import subprocess
import sys
from pathlib import Path

DEFAULT_REPOS = ["~/code/open-net-worth", "~/code/open-net-worth-www", "~/code/onw-cloud"]
IMAGE_HINTS = re.compile(
    r"(logo|favicon|apple-touch|icon-\d|icon-maskable|og\.(png|svg)|trayTemplate|app-icon)", re.I
)


def fingerprints(svg: str) -> list[str]:
    marks = [m.strip() for m in re.findall(r"<title>([^<]{6,})</title>", svg)]
    marks += [d[:32] for d in re.findall(r'\bd="([^"]{40,})"', svg)]
    return [m for m in dict.fromkeys(marks) if m]


def tracked(repo: Path) -> list[str]:
    out = subprocess.run(
        ["git", "-C", str(repo), "ls-files"], capture_output=True, text=True, check=True
    )
    return out.stdout.splitlines()


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    marks = fingerprints(Path(sys.argv[1]).read_text(encoding="utf-8"))
    if not marks:
        print("No fingerprint found in the old SVG (no <title> or long path data).")
        return 2
    found = 0
    for repo in [Path(r).expanduser() for r in (sys.argv[2:] or DEFAULT_REPOS)]:
        if not (repo / ".git").exists():
            print(f"skip {repo} (not a git checkout)")
            continue
        files = tracked(repo)
        for rel in files:
            path = repo / rel
            if (
                path.suffix.lower() in {".png", ".ico", ".icns", ".jpg", ".webp", ".car"}
                or "node_modules" in rel
            ):
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            hits = [m for m in marks if m in text]
            if hits:
                found += 1
                print(f"OLD MARK  {repo.name}/{rel}")
        images = [
            rel
            for rel in files
            if IMAGE_HINTS.search(Path(rel).name) and "connector-logos" not in rel
        ]
        print(f"\n{repo.name}: brand images to eyeball ({len(images)}):")
        for rel in images:
            print(f"  {rel}")
        print()
    print(
        "No text file still contains the old mark."
        if not found
        else f"{found} file(s) still contain the old mark."
    )
    return 1 if found else 0


if __name__ == "__main__":
    raise SystemExit(main())
