#!/usr/bin/env python3
"""Append a one-line bug/idea to the Hardening Board inbox — frictionless capture.

Usage:  python3 scripts/add_bug.py "net worth wrong with a USD account"
Invoked via:  make bug MSG="..."

Newest entries land directly under the Inbox header. The empty-state hint line is
dropped once a real entry exists. Plain write (this is a docs file, not a data store).
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

BOARD = Path(__file__).resolve().parent.parent / "docs" / "launch" / "BOARD.md"
INBOX_MARKER = "## 📥 Inbox"
PLACEHOLDER_PREFIX = "- _(nothing captured yet"


def main(argv: list[str]) -> int:
    msg = " ".join(argv[1:]).strip()
    if not msg:
        print('usage: make bug MSG="what is wrong"', file=sys.stderr)
        return 2
    if not BOARD.exists():
        print(f"board not found: {BOARD}", file=sys.stderr)
        return 1

    lines = BOARD.read_text(encoding="utf-8").splitlines()
    entry = f"- 🆕 {msg} — {date.today().isoformat()}"

    out: list[str] = []
    inserted = False
    for line in lines:
        if line.startswith(PLACEHOLDER_PREFIX):
            continue  # drop the empty-state hint once a real bug lands
        out.append(line)
        if not inserted and line.startswith(INBOX_MARKER):
            out.append(entry)
            inserted = True

    if not inserted:
        print(f"inbox marker '{INBOX_MARKER}' not found in {BOARD}", file=sys.stderr)
        return 1

    BOARD.write_text("\n".join(out) + "\n", encoding="utf-8")
    print(f"captured → {entry}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
