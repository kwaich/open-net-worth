#!/usr/bin/env python3
"""Claude Code PreToolUse hook: run the privacy guard before an agent commits, pushes or opens a PR.

Git hooks only run for a `git commit` in a clone that installed them; an AI agent in a fresh
cloud clone, or one committing through plumbing (`git commit-tree` + `update-ref`), skips them
entirely. This hook sits on the agent's own Bash tool instead (wired in .claude/settings.json):

* `git commit`              → the staged changes (`privacy_guard --staged`)
* `git push`, `gh pr create` → every commit about to leave the machine
                              (`privacy_guard --range <upstream>..HEAD`), which also covers
                              plumbing commits made earlier

Exit 2 blocks the tool call and hands the findings back to the agent. Anything else (no git
write, guard unavailable) exits 0: the CI privacy job is the backstop that cannot be skipped.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMMIT = re.compile(r"\bgit\b[^|;&\n]*\scommit(?![-\w])")
PUBLISH = re.compile(r"\bgit\b[^|;&\n]*\spush\b|\bgh\s+pr\s+create\b")


def _upstream_base() -> str:
    for ref in ("@{upstream}", "origin/main"):
        r = subprocess.run(
            ["git", "rev-parse", "--verify", "--quiet", ref],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        if r.returncode == 0:
            return r.stdout.strip()
    return "HEAD~1"


def _guard(*args: str) -> subprocess.CompletedProcess:
    python = ROOT / ".venv" / "bin" / "python3"
    exe = str(python) if python.exists() else sys.executable
    return subprocess.run(
        [exe, "-m", "backend.scripts.privacy_guard", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except ValueError:
        return 0
    command = str((payload.get("tool_input") or {}).get("command") or "")
    if PUBLISH.search(command):
        result = _guard("--range", f"{_upstream_base()}..HEAD")
    elif COMMIT.search(command):
        result = _guard("--staged")
    else:
        return 0
    if result.returncode == 1:
        sys.stderr.write(
            result.stdout
            + "\nBlocked by the privacy guard: remove the real data (use synthetic values), "
            "then retry. Do not bypass this check.\n"
        )
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
