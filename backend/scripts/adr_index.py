"""Keep `docs/adr/README.md`'s index COMPLETE — every ADR file has a row.

Why a gate and not a generator: the Title column is hand-written prose, often several
sentences that carry the decision's substance. Regenerating it from each file's H1 would
DESTROY that. So this checks the one property that actually rotted — coverage — and, when
asked to write, appends only the rows that are missing, seeded with the ADR's own H1 for a
human to enrich later.

The index had drifted ~170 entries behind by 2026-08-27, back to July, because nothing ever
checked it. Same shape as the other ratchets in this repo (`tool_inventory --lint`,
`check-css-classes`): the rule is enforced, not remembered.

    python -m backend.scripts.adr_index --check   # exit 1 listing any ADR with no row
    python -m backend.scripts.adr_index --write   # append the missing rows
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
ADR_DIR = REPO_ROOT / "docs" / "adr"
INDEX = ADR_DIR / "README.md"

_ROW = re.compile(r"^\|\s*\[([^\]]+)\]\(([^)]+)\)")
# Three header shapes exist in this corpus and all three are legitimate history:
#   `_Status: Accepted · 2026-08-27_`  ·  `- **Status:** Accepted`  ·  `- Status: Accepted`
_STATUS = re.compile(r"(?:_Status:\s*|[-*]\s*\**Status:?\**:?\s*)([A-Za-z]+)", re.I)


def adr_files() -> list[Path]:
    return sorted(p for p in ADR_DIR.glob("*.md") if p.name != "README.md")


def indexed_slugs() -> set[str]:
    text = INDEX.read_text(encoding="utf-8")
    out: set[str] = set()
    for line in text.splitlines():
        m = _ROW.match(line)
        if m:
            out.add(m.group(2).removesuffix(".md"))
    return out


def _title(path: Path) -> str:
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("# "):
            return line[2:].strip().replace("|", "\\|")
    return path.stem


def _status(path: Path) -> str:
    m = _STATUS.search(path.read_text(encoding="utf-8"))
    return m.group(1).capitalize() if m else "Accepted"


def _date(path: Path) -> str:
    m = re.match(r"(\d{4}-\d{2}-\d{2})", path.stem)
    if m:
        return m.group(1)
    body = path.read_text(encoding="utf-8")
    d = re.search(r"(\d{4}-\d{2}-\d{2})", body)
    return d.group(1) if d else ""


def missing() -> list[Path]:
    have = indexed_slugs()
    return [p for p in adr_files() if p.stem not in have]


def write_missing() -> int:
    rows = missing()
    if not rows:
        return 0
    lines = INDEX.read_text(encoding="utf-8").rstrip("\n").splitlines()
    for p in rows:
        lines.append(f"| [{p.stem}]({p.name}) | {_title(p)} | {_status(p)} | {_date(p)} |")
    INDEX.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return len(rows)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args()

    if args.write:
        n = write_missing()
        print(f"adr_index: appended {n} row(s)")
        return 0

    gaps = missing()
    if gaps:
        print(f"adr_index: {len(gaps)} ADR(s) missing from docs/adr/README.md:", file=sys.stderr)
        for p in gaps[:20]:
            print(f"  - {p.name}", file=sys.stderr)
        if len(gaps) > 20:
            print(f"  … and {len(gaps) - 20} more", file=sys.stderr)
        print("  fix: python -m backend.scripts.adr_index --write", file=sys.stderr)
        return 1
    print(f"adr_index: clean ({len(adr_files())} ADRs, all indexed)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
