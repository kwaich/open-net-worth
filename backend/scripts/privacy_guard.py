"""Refuse to commit or merge anything that could carry someone's real financial life.

This is a personal-finance app built largely with AI agents, and its own workflow keeps
real data close at hand: a live book under ~/.open-net-worth, real statements for QA,
screenshots of real screens. ``pii_lint`` catches values SHAPED like an ID, phone or account
number; ``gitleaks`` catches secrets. This guard closes the rest:

* FILES that should never be in the repo: databases, bank exports (OFX/QFX/QIF/MT940),
  key and env files, and statements/spreadsheets/images outside the few folders where
  synthetic fixtures and product art legitimately live. Widening those folders is a reviewed
  change to this file, never a flag.
* LOCAL PATHS (``/Users/<name>/``, ``/home/<name>/``, ``C:\\Users\\<name>\\``): they name a real
  machine user and usually mean something was pasted from a real session.
* A PRIVATE DENYLIST of the owner's own identifiers (names, emails, account nicknames, real
  merchants, holdings). The list never lives in the repo — a public file listing them would
  BE the leak. Locally it is ``~/.config/open-net-worth/privacy-denylist.txt`` (or
  ``$ONW_PRIVACY_DENYLIST``); in CI it is the ``PRIVACY_DENYLIST`` secret. Findings report
  "denylist entry #N", never the term, so a CI log on a public repo can't leak it either.

Modes::

    python -m backend.scripts.privacy_guard --staged            # pre-commit / agent hook
    python -m backend.scripts.privacy_guard --range BASE..HEAD  # CI: a PR's or push's commits
    python -m backend.scripts.privacy_guard --tree              # every tracked file
    python -m backend.scripts.privacy_guard --worktree          # all not yet in HEAD (agents)

Content rules look only at ADDED lines in --staged/--range, so history-free review stays fast
and an existing line is never re-flagged. A genuinely synthetic match is allowed with
``privacy-guard: ok`` on the same line. Exit 1 on any finding.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ALLOW_MARKER = "privacy-guard: ok"

# Never in the repo, wherever they are.
BLOCKED_SUFFIXES = {
    ".db",
    ".sqlite",
    ".sqlite3",
    ".db-wal",
    ".db-shm",
    ".db-journal",
    ".pem",
    ".key",
    ".p12",
    ".pfx",
    ".keychain",
    ".kdbx",
}
ENV_FILE = re.compile(r"(^|/)\.env(\.[^/]*)?$")
ENV_ALLOWED = {".env.example"}

# Allowed only inside the folders that hold synthetic fixtures or product art.
# Statements, exports and spreadsheets: allowed only where synthetic fixtures live.
DATA_SUFFIXES = {
    ".pdf",
    ".csv",
    ".tsv",
    ".xlsx",
    ".xls",
    ".numbers",
    ".ods",
    ".ofx",
    ".qfx",
    ".qif",
    ".mt940",
    ".sta",
}
DATA_DIRS = ("tests/fixtures/", "qa/browser-use/fixtures/", "backend/scripts/demo/")
IMAGE_SUFFIXES = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".heic",
    ".heif",
    ".tif",
    ".tiff",
    ".bmp",
    ".mp4",
    ".mov",
    ".webm",
}
IMAGE_DIRS = (
    "frontend/public/",
    "frontend/src/assets/",
    "docs-site/",
    "docs/product/",
    "desktop/resources/",
    "backend/scripts/demo/",
    "qa/browser-use/fixtures/",
    "tests/fixtures/",
)

# A real machine user's home. Generic placeholders (/Users/you, /home/runner, $HOME…) pass.
_GENERIC_USERS = (
    r"(?:you|me|user|username|name|owner|onw|app|runner|test|tester|x|victim|example|someone|"
    r"alice|bob|jane|john|foo|bar|<[^>]*>|\$\w+|\$\{[^}]*\}|%\w+%|\*|\.\.\.)"
)
LOCAL_PATH = re.compile(
    # POSIX homes are case-sensitive: "PATH/HOME/TMPDIR" in prose is not a path.
    r"(?:/Users|/home)/(?!(?i:" + _GENERIC_USERS + r")[\\/])[A-Za-z0-9._-]+/"
    r"|(?i:[A-Za-z]:\\{1,2}Users\\{1,2})(?!(?i:" + _GENERIC_USERS + r")[\\/])[A-Za-z0-9._ -]+\\"
)
# Files whose whole point is a list of fake private values (the redaction fixtures).
EXEMPT_PATHS = {
    "backend/scripts/privacy_guard.py",  # its own docs name the placeholders it allows
    "tests/fixtures/problem_report_redaction.json",  # fake paths the redactor must remove
}


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout


def _denylist() -> list[str]:
    raw = os.environ.get("PRIVACY_DENYLIST", "")
    path = Path(
        os.environ.get("ONW_PRIVACY_DENYLIST")
        or Path.home() / ".config" / "open-net-worth" / "privacy-denylist.txt"
    )
    if path.is_file():
        raw += "\n" + path.read_text(encoding="utf-8")
    terms = []
    for line in re.split(r"[\n,]", raw):
        term = line.strip()
        if term and not term.startswith("#") and len(term) >= 3:
            terms.append(term)
    return terms


def _changed_files(mode: str, rng: str | None) -> list[str]:
    if mode == "staged":
        out = _git("diff", "--cached", "--name-only", "--diff-filter=ACMR")
    elif mode == "range":
        out = _git("diff", "--name-only", "--diff-filter=ACMR", rng or "")
    elif mode == "worktree":
        # Everything not yet in HEAD, tracked or not — what any commit from here could carry,
        # whichever index it uses (an agent's plumbing commit-tree never runs git hooks).
        out = _git("diff", "--name-only", "--diff-filter=ACMR", "HEAD")
        out += _git("ls-files", "--others", "--exclude-standard")
    else:
        out = _git("ls-files")
    return [p for p in out.splitlines() if p]


def _added_lines(mode: str, rng: str | None, path: str) -> list[tuple[int, str]]:
    """(line number, text) of every line this change ADDS to ``path`` (all lines for --tree)."""
    untracked = mode == "worktree" and not _git("ls-files", "--", path).strip()
    if mode == "tree" or untracked:
        try:
            text = (ROOT / path).read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            return []
        return list(enumerate(text.splitlines(), start=1))
    args = ["diff", "-U0", "--no-color"]
    args += {"staged": ["--cached"], "worktree": ["HEAD"]}.get(mode, [rng or ""])
    try:
        diff = _git(*args, "--", path)
    except subprocess.CalledProcessError:
        return []
    out: list[tuple[int, str]] = []
    line_no = 0
    for row in diff.splitlines():
        hunk = re.match(r"@@ -\d+(?:,\d+)? \+(\d+)", row)
        if hunk:
            line_no = int(hunk.group(1))
        elif row.startswith("+") and not row.startswith("+++"):
            out.append((line_no, row[1:]))
            line_no += 1
    return out


def check_path(path: str) -> str | None:
    """Why ``path`` may not be committed, or None."""
    lower = path.lower()
    suffix = Path(lower).suffix
    if suffix in BLOCKED_SUFFIXES:
        return f"{suffix} files never belong in the repo (databases, keys)"
    if ENV_FILE.search(lower) and Path(path).name not in ENV_ALLOWED:
        return "env files hold secrets; commit a .env.example instead"
    if suffix in DATA_SUFFIXES and not lower.startswith(DATA_DIRS):
        return (
            f"{suffix} outside the synthetic-fixture folders — a statement or export is "
            "almost always real; put a synthetic one under tests/fixtures/"
        )
    if suffix in IMAGE_SUFFIXES and not lower.startswith(IMAGE_DIRS):
        return (
            "image outside the product-art folders — screenshots of a real book stay out; "
            "QA evidence belongs in a gitignored folder"
        )
    return None


def scan(mode: str, rng: str | None = None) -> list[str]:
    findings: list[str] = []
    terms = _denylist()
    patterns = [
        re.compile(r"(?<![A-Za-z0-9])" + re.escape(t) + r"(?![A-Za-z0-9])", re.IGNORECASE)
        for t in terms
    ]
    for path in _changed_files(mode, rng):
        if path in EXEMPT_PATHS:
            continue
        reason = check_path(path)
        if reason:
            findings.append(f"{path}: {reason}")
            continue
        for line_no, text in _added_lines(mode, rng, path):
            if ALLOW_MARKER in text:
                continue
            if LOCAL_PATH.search(text):
                findings.append(f"{path}:{line_no}: a local home path names a real machine user")
            for i, pat in enumerate(patterns, start=1):
                if pat.search(text):
                    findings.append(f"{path}:{line_no}: matches private denylist entry #{i}")
    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--staged", action="store_true", help="staged changes (pre-commit)")
    group.add_argument("--range", metavar="BASE..HEAD", help="a commit range (CI)")
    group.add_argument("--tree", action="store_true", help="every tracked file")
    group.add_argument(
        "--worktree", action="store_true", help="everything not yet in HEAD (agent hook)"
    )
    args = parser.parse_args()
    mode = (
        "staged"
        if args.staged
        else "range"
        if args.range
        else "worktree"
        if args.worktree
        else "tree"
    )
    findings = scan(mode, args.range)
    if not findings:
        terms = len(_denylist())
        print(f"privacy-guard: clean ({terms} private denylist entr{'y' if terms == 1 else 'ies'})")
        return 0
    print("privacy-guard: refused — this change may carry real personal or financial data:")
    for f in findings:
        print(f"  {f}")
    print(f"\nA synthetic value that trips a rule: add `{ALLOW_MARKER}` on its line.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
