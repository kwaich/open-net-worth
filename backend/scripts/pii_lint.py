"""Refuse to ship personal data.

Why this exists: three privacy sweeps (2026-08-24) missed the owner's national ID and mobile
number because the code's OWN COMMENTS mislabelled them — the FIN was described as a "tax
reference" and the phone as a "destination account number", so every grep for `NRIC` or `phone`
came back clean. A fourth leak then landed three days AFTER those sweeps, in an ADR, a test
docstring, and a shipped code comment simultaneously, all from one debugging session.

The root cause is a documentation convention, not carelessness: this repo documents real bugs
with real data ("owner repro, DBS June statement"). That convention is good for engineering and
fatal for privacy, so the guard has to sit at the commit boundary rather than rely on intent.

What this checks is SHAPE, never a label — that is the whole lesson. A value is flagged for
looking like a national ID or a phone number, regardless of what the surrounding prose calls it.

Usage
-----
    .venv/bin/python3 -m backend.scripts.pii_lint            # scan the working tree
    .venv/bin/python3 -m backend.scripts.pii_lint --staged   # pre-commit: staged files only

Exit code 1 on any finding. To allow a genuinely synthetic value that trips a pattern, add an
`# pii-lint: ok` comment on the same line (or `<!-- pii-lint: ok -->` in markdown).
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

ALLOW_MARKER = "pii-lint: ok"

# Directories that never ship, plus the two audit documents that describe the problem.
SKIP_DIRS = {
    ".git",
    "node_modules",
    ".venv",
    "__pycache__",
    "dist",
    "dist-embed",
    ".pytest_cache",
    "coverage",
    ".astro",
    ".mypy_cache",
    ".import_linter_cache",
    ".ruff_cache",
    # Disposable full-repo copies from agent sessions. They are gitignored, so they never
    # ship — but they hold stale copies of whatever the tree looked like at the time.
    "worktrees",
}
# Matched by repo-relative path, not bare name: "out" and "runtime" are too generic to skip
# everywhere. The desktop build output and its downloaded Python runtime are gitignored
# (desktop/.gitignore) and hold only vendored third-party files.
SKIP_REL_DIRS = {"desktop/out", "desktop/runtime"}
SKIP_NAMES = {"package-lock.json", "pnpm-lock.yaml", "yarn.lock"}
# Generated/minified files are megabytes of base64 and vendor code; scanning them costs
# minutes and finds only false positives.
MAX_BYTES = 512_000
SKIP_FILES = {
    "backend/scripts/pii_lint.py",  # this file quotes the patterns
    "docs/security/LAUNCH_PII_CHECKLIST.md",
    "docs/security/2026-08-25-pentest-audit.md",
    "docs/launch/PUBLISH_CHECKLIST.md",
}
SCAN_EXT = {".py", ".ts", ".tsx", ".jsx", ".js", ".md", ".json", ".html", ".mjs", ".csv"}

# Each rule is (name, compiled pattern, note). Patterns match SHAPE — a hit is a prompt to
# look, not proof of a leak; use the allow marker once a value is confirmed synthetic.
RULES: list[tuple[str, re.Pattern[str], str]] = [
    (
        "singapore-nric-fin",
        re.compile(r"\b[STFGM]\d{7}[A-Z]\b"),
        "looks like an NRIC/FIN (national identity number)",
    ),
    (
        "singapore-mobile",
        re.compile(r"(?<![\d.])\b[89]\d{7}\b(?![\d.])"),
        "looks like a Singapore mobile number",
    ),
    (
        "vietnam-mobile",
        re.compile(r"(?<![\d.])\b0[35789]\d{8}\b(?![\d.])"),
        "looks like a Vietnamese mobile number",
    ),
    # The two shapes below are only meaningful WITH context: an 11-19 digit run is just as
    # likely a timestamp, a snowflake id, or a hash fragment. Requiring a nearby account word
    # is what keeps this rule at a signal level anyone will actually act on — an unusable
    # linter gets disabled, which is worse than no linter.
    (
        "bank-account-long",
        re.compile(
            r"(?:account|acct|a/c|iban|card|beneficiary|\btoi\b|\btu\b)[^\n]{0,40}?"
            r"(?<![\d.\-])(\d{11,19})(?![\d.\-])",
            re.I,
        ),
        "looks like a bank account or card number in an account context",
    ),
    (
        "iban",
        re.compile(r"\biban\b[^\n]{0,20}?([A-Z]{2}\d{2}[A-Z0-9]{11,30})\b", re.I),
        "looks like an IBAN",
    ),
    (
        "dbs-account-format",
        re.compile(r"\b\d{3}-\d{5,7}-\d\b"),
        "looks like a bank account number (xxx-xxxxxxx-x)",
    ),
    (
        "bank-alert-sender",
        re.compile(
            r"[\w.+-]+@(?:dbs|citibank|uob|ocbc|lark|vietcombank)\.(?!example|test|invalid)[\w.]+",
            re.I,
        ),
        "quotes a real bank's alert sender address",
    ),
]

# Values that legitimately trip a shape rule. Keep this list SHORT and justified — every entry
# is a hole. Prefer the inline `pii-lint: ok` marker, which keeps the justification next to
# the value it excuses.
GLOBAL_ALLOW = {
    "86400000",  # milliseconds per day
    "31536000",  # seconds per year
    # The synthetic stand-ins this repo uses in fixtures. They deliberately keep the SHAPE of
    # the thing they replace, so they trip the shape rules by design.
    "S1234567D",  # synthetic NRIC/FIN
    "81110000",  # synthetic SG mobile (owner slot)
    "82220000",  # synthetic SG mobile (third party)
    "93330000",  # synthetic SG mobile (third party)
    "81234567",
    "91234567",
    "0300000000",  # synthetic VN mobile
    "0391234567",
    "001199012345",  # synthetic VN national id
}

# Numbers that are obviously placeholders: repeated digits, or a 1234567-style run.
_PLACEHOLDER = re.compile(r"^(\d)\1+$|0123456|1234567|2345678|9876543|(\d)\2{3,}")


def _is_placeholder(value: str) -> bool:
    digits = value.strip("+-")
    return bool(_PLACEHOLDER.search(digits)) or value in GLOBAL_ALLOW


def _iter_files(staged: bool) -> list[Path]:
    if staged:
        out = subprocess.run(
            ["git", "diff", "--cached", "--name-only", "--diff-filter=ACM"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        ).stdout
        return [ROOT / line for line in out.splitlines() if line.strip()]
    # os.walk with in-place pruning, not rglob: rglob descends into node_modules/.venv before
    # the filter sees them, which turns a 2-second scan into minutes.
    import os

    found: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(ROOT):
        rel_dir = Path(dirpath).relative_to(ROOT)
        dirnames[:] = [
            d
            for d in dirnames
            if d not in SKIP_DIRS and (rel_dir / d).as_posix() not in SKIP_REL_DIRS
        ]
        for filename in filenames:
            if filename in SKIP_NAMES or Path(filename).suffix not in SCAN_EXT:
                continue
            candidate = Path(dirpath) / filename
            try:
                if candidate.stat().st_size > MAX_BYTES:
                    continue
            except OSError:
                continue
            found.append(candidate)
    return found


def scan(staged: bool = False) -> list[str]:
    findings: list[str] = []
    for path in _iter_files(staged):
        if not path.is_file() or path.suffix not in SCAN_EXT:
            continue
        try:
            rel = path.relative_to(ROOT).as_posix()
        except ValueError:
            continue
        if rel in SKIP_FILES:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            # A 2000+ char line is minified or a data blob, never hand-written prose that
            # could carry a real statement row.
            if len(line) > 2000 or ALLOW_MARKER in line:
                continue
            for name, pattern, note in RULES:
                for match in pattern.finditer(line):
                    # Context-bearing rules capture the identifier in group 1; the keyword
                    # that gave it meaning is not itself the finding.
                    value = match.group(1) if match.groups() else match.group(0)
                    if _is_placeholder(value):
                        continue
                    findings.append(f"{rel}:{lineno}: [{name}] {note}: {value!r}")
    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--staged", action="store_true", help="scan staged files only")
    args = parser.parse_args()

    findings = scan(staged=args.staged)
    if not findings:
        print("pii-lint: clean")
        return 0

    print(f"pii-lint: {len(findings)} possible personal-data value(s)\n", file=sys.stderr)
    for finding in findings:
        print(f"  {finding}", file=sys.stderr)
    print(
        "\nReproduce bugs with SYNTHETIC data. Never paste a real statement row, account "
        "number, phone number, or national id into a test, doc, or comment.\n"
        "If a flagged value is genuinely synthetic, add `pii-lint: ok` on that line.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
