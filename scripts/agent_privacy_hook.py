#!/usr/bin/env python3
"""Claude Code PreToolUse hook: run the privacy guard before an agent commits, pushes or opens a PR.

Git hooks only run for a `git commit` in a clone that installed them; an AI agent in a fresh
cloud clone, or one committing through plumbing (`git commit-tree` + `update-ref`), skips them
entirely. This hook sits on the agent's own Bash tool instead (wired in .claude/settings.json):

* `git commit`              → everything not yet in HEAD, tracked or untracked
                              (`privacy_guard --worktree`). The hook runs BEFORE the command, so
                              in `git add x && git commit` nothing is staged yet and `--staged`
                              would scan nothing.
* `git push`, `gh pr create` → every commit about to leave the machine, each against its own
                              parent (`privacy_guard --range <upstream>..HEAD`), which also
                              covers plumbing commits made earlier and a file added then deleted.
                              With no upstream and no origin/main the whole history is scanned.

A command that does both (`git commit … && git push`) runs both checks.

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


def _upstream_base() -> str | None:
    for ref in ("@{upstream}", "origin/main"):
        r = subprocess.run(
            ["git", "rev-parse", "--verify", "--quiet", ref],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        if r.returncode == 0:
            return r.stdout.strip()
    return None


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


def guard_runs(command: str) -> list[tuple[str, ...]]:
    """The privacy_guard argument lists a Bash command needs before it may run."""
    runs: list[tuple[str, ...]] = []
    if COMMIT.search(command):
        runs.append(("--worktree",))
    if PUBLISH.search(command):
        base = _upstream_base()
        runs.append(("--range", f"{base}..HEAD" if base else "HEAD"))
    return runs


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except ValueError:
        return 0
    command = str((payload.get("tool_input") or {}).get("command") or "")
    results = [_guard(*args) for args in guard_runs(command)]
    refused = [r.stdout for r in results if r.returncode == 1]
    if refused:
        sys.stderr.write(
            "\n".join(refused)
            + "\nBlocked by the privacy guard: remove the real data (use synthetic values), "
            "then retry. Do not bypass this check.\n"
        )
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
