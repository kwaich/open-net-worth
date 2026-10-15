#!/usr/bin/env python3
"""commit-msg hook: a user-facing change must say what happened to the product docs.

If the staged change touches user-facing code (docs/docs-map.json) and no product doc, the
commit message must carry a trailer line:
    Docs: updated            (docs changed in this commit — normally the docs files are staged)
    Docs: none — <reason>    (deliberately no doc impact, e.g. internal refactor)
Merge and revert commits are exempt. Stdlib only.
"""

from __future__ import annotations

import fnmatch
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOC_PREFIXES = ("docs/product/", "README.md", "desktop/README.md", "docs/ai/")
TRAILER = re.compile(r"^Docs:\s*(updated|none\b.*)$", re.IGNORECASE | re.MULTILINE)


def staged() -> list[str]:
    out = subprocess.run(
        ["git", "diff", "--cached", "--name-only"], capture_output=True, text=True, cwd=ROOT
    )
    return [line for line in out.stdout.splitlines() if line]


def is_test(path: str) -> bool:
    name = Path(path).name
    return ".test." in name or name.startswith("test_") or "/tests/" in f"/{path}" or "/qa/" in path


def match(path: str, areas: list[dict]) -> list[str] | None:
    for area in areas:
        if fnmatch.fnmatch(path, area["glob"]):
            return area["docs"]
    return None


def main(message_file: str) -> int:
    message = Path(message_file).read_text(encoding="utf-8")
    first = message.lstrip().splitlines()[0] if message.strip() else ""
    if first.startswith(("Merge ", "Revert ")) or TRAILER.search(message):
        return 0
    files = staged()
    if any(f.startswith(DOC_PREFIXES) for f in files):
        return 0
    areas = json.loads((ROOT / "docs/docs-map.json").read_text(encoding="utf-8"))["areas"]
    hits: dict[str, list[str]] = {}
    for f in files:
        if is_test(f):
            continue
        docs = match(f, areas)
        if docs:
            for d in docs:
                hits.setdefault(d, []).append(f)
    if not hits:
        return 0
    print(
        "\ndocs-impact: this commit changes user-facing code but no product doc.\n", file=sys.stderr
    )
    for doc, sources in hits.items():
        print(f"  check {doc}  ← {', '.join(sorted(set(sources))[:4])}", file=sys.stderr)
    print(
        "\nEither update those docs in this commit, or add a trailer to the message:\n"
        "  Docs: updated\n  Docs: none — <why there is no user-visible change>\n",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
