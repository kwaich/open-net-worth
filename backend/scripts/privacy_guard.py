"""Refuse to commit or merge anything that could carry someone's real financial life.

This is a personal-finance app built largely with AI agents, and its own workflow keeps
real data close at hand: a live book under ~/.open-net-worth, real statements for QA,
screenshots of real screens. ``pii_lint`` catches values SHAPED like an ID, phone or account
number; ``gitleaks`` catches secrets. This guard closes the rest:

* FILES that should never be in the repo: databases, bank exports (OFX/QFX/QIF/MT940),
  key and env files, archives and backups (a book export is a ``.zip``; its loose JSON form is
  caught too), and statements/spreadsheets/images outside the few folders where synthetic
  fixtures and product art legitimately live. Widening those folders is a reviewed change to
  this file, never a flag.
* LOCAL PATHS (``/Users/<name>/``, ``/home/<name>/``, ``C:\\Users\\<name>\\``): they name a real
  machine user and usually mean something was pasted from a real session.
* A PRIVATE DENYLIST of the owner's own identifiers (names, emails, account nicknames, real
  merchants, holdings). The list never lives in the repo — a public file listing them would
  BE the leak. Locally it is ``~/.config/open-net-worth/privacy-denylist.txt`` (or
  ``$ONW_PRIVACY_DENYLIST``); in CI it is the ``PRIVACY_DENYLIST`` secret. Findings report
  "denylist entry #N", never the term, so a CI log on a public repo can't leak it either.
  Matching is accent- and case-insensitive on both sides (a term with diacritics also
  catches its plain-ASCII and decomposed spellings), with Unicode-aware word boundaries.

Modes::

    python -m backend.scripts.privacy_guard --staged            # pre-commit / agent hook
    python -m backend.scripts.privacy_guard --range BASE..HEAD  # CI: a PR's or push's commits
    python -m backend.scripts.privacy_guard --tree              # every tracked file
    python -m backend.scripts.privacy_guard --worktree          # all not yet in HEAD (agents)

--range scans EACH commit against its own parent, so a file added in one commit and deleted in
the next is still caught (it would be published by the push). Content rules look only at ADDED
lines, so an existing line is never re-flagged. A synthetic home path is allowed with
``privacy-guard: ok`` on the same line; the marker never silences a private-denylist match
(except in LICENSE and NOTICE, whose copyright line names a real holder by design). Exit 1 on
any finding; ``--require-denylist`` also fails when no denylist entries were loaded (CI on main).
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import unicodedata
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
# Exempts a line from the local-home-path rule ONLY. It never silences a private-denylist match:
# a marker an agent can type on any line would turn the owner's own identifiers into a bypass.
ALLOW_MARKER = "privacy-guard: ok"
# The one place the marker also covers the denylist: a copyright line names a real holder.
DENYLIST_MARKER_PATHS = {"LICENSE", "NOTICE"}

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
# Archives and backups: a book export is a .zip, a hand-made copy is a .bak. Allowed only where
# synthetic fixtures live (DATA_DIRS).
ARCHIVE_SUFFIXES = {
    ".zip",
    ".tar",
    ".tgz",
    ".gz",
    ".bz2",
    ".xz",
    ".zst",
    ".7z",
    ".rar",
    ".bak",
    ".backup",
    ".dump",
}
# A book export unpacked or renamed: its JSON files and manifest (services/portability.py).
BACKUP_JSON_NAME = re.compile(r"backup|export|onw-book|sanitis|sanitiz", re.IGNORECASE)
BACKUP_MANIFEST_KEYS = ('"schema_version"', '"exported_at"', '"blob_trees"')
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
# Files whose whole point is a list of fake private values (the redaction fixtures). Exempt from
# the local-path rule only — the denylist and file rules still apply to them.
EXEMPT_PATHS = {
    "backend/scripts/privacy_guard.py",  # its own docs name the placeholders it allows
    "tests/fixtures/problem_report_redaction.json",  # fake paths the redactor must remove
}


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", "--literal-pathspecs", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    ).stdout


def _denylist_path() -> Path:
    return Path(
        os.environ.get("ONW_PRIVACY_DENYLIST")
        or Path.home() / ".config" / "open-net-worth" / "privacy-denylist.txt"
    )


def _denylist() -> list[str]:
    raw = os.environ.get("PRIVACY_DENYLIST", "")
    path = _denylist_path()
    if path.is_file():
        raw += "\n" + path.read_text(encoding="utf-8")
    terms = []
    for line in re.split(r"[\n,]", raw):
        term = line.strip()
        if term and not term.startswith("#") and len(term) >= 3:
            terms.append(term)
    return terms


# Letters NFKD leaves whole, folded by hand so "đ" meets "d".
_UNDECOMPOSED = str.maketrans({"đ": "d", "ø": "o", "ł": "l"})


def _fold(text: str) -> str:
    """Case-, accent- and spacing-insensitive form of ``text``, used on terms AND scanned lines."""
    folded = text.casefold()
    if not folded.isascii():
        decomposed = unicodedata.normalize("NFKD", folded)
        kept = "".join(c for c in decomposed if unicodedata.category(c) not in ("Mn", "Cf"))
        folded = kept.translate(_UNDECOMPOSED)
    return re.sub(r"\s+", " ", folded)


def _patterns(terms: list[str]) -> list[re.Pattern[str]]:
    # [^\W_] is "a Unicode letter or digit": a term only matches as a whole word.
    return [re.compile(r"(?<![^\W_])" + re.escape(_fold(t)) + r"(?![^\W_])") for t in terms]


@dataclass(frozen=True)
class _Source:
    """One set of changes to review: its file names and how to read what each one adds."""

    label: str  # a commit's short sha in --range, else ""
    names: tuple[str, ...]
    diff_args: tuple[str, ...] = ()  # `git <these> -- <path>` prints the path's patch
    whole: frozenset[str] = frozenset()  # files read from disk in full (untracked, --tree)


def _names(*args: str) -> tuple[str, ...]:
    return tuple(n for n in _git(*args, "-z").split("\0") if n)


def _base() -> str:
    """HEAD, or the empty tree in a repository with no commit yet."""
    try:
        return _git("rev-parse", "--verify", "--quiet", "HEAD").strip()
    except subprocess.CalledProcessError:
        return _git("hash-object", "-t", "tree", "/dev/null").strip()


def _commit_diff_args(sha: str) -> tuple[str, ...]:
    # --cc makes a merge show only what it adds over EVERY parent (a conflict resolution);
    # --root lets a first commit diff against nothing.
    return ("diff-tree", "--root", "--cc", "-r", "-p", "-U0", "--no-color", "--no-commit-id", sha)


def _sources(mode: str, rng: str | None) -> Iterator[_Source]:
    if mode == "staged":
        yield _Source(
            "",
            _names("diff", "--cached", "--name-only", "--diff-filter=ACMR"),
            ("diff", "--cached", "-U0", "--no-color"),
        )
    elif mode == "worktree":
        # Everything not yet in HEAD, tracked or not — what any commit from here could carry,
        # whichever index it uses (an agent's plumbing commit-tree never runs git hooks).
        base = _base()
        untracked = _names("ls-files", "--others", "--exclude-standard")
        tracked = _names("diff", "--name-only", "--diff-filter=ACMR", base)
        yield _Source(
            "",
            tracked + untracked,
            ("diff", "-U0", "--no-color", base),
            frozenset(untracked),
        )
    elif mode == "range":
        # Each commit against its own parent: a file added and later deleted inside the range
        # is gone from the net diff but still published with the push.
        if not rng or rng.startswith("-"):
            raise ValueError(f"not a commit range: {rng!r}")
        for sha in _git("rev-list", "--reverse", rng).split():
            names = _names(
                "diff-tree",
                "--root",
                "--cc",
                "-r",
                "--name-only",
                "--no-commit-id",
                "--diff-filter=ACMR",
                sha,
            )
            yield _Source(sha[:9], names, _commit_diff_args(sha))
    else:
        names = _names("ls-files")
        yield _Source("", names, whole=frozenset(names))


_HUNK = re.compile(r"^(@@+) .*? \+(\d+)(?:,\d+)? @@+")


def _parse_added(patch: str) -> list[tuple[int, str]]:
    """(line number, text) of each line a patch adds — over every parent, for a combined diff."""
    out: list[tuple[int, str]] = []
    width = line_no = 0
    in_hunk = False
    for row in patch.splitlines():
        hunk = _HUNK.match(row)
        if hunk:
            in_hunk, width, line_no = True, len(hunk.group(1)) - 1, int(hunk.group(2))
        elif row.startswith("diff "):
            in_hunk = False
        elif in_hunk and not row.startswith("\\"):
            prefix = row[:width]
            if "-" in prefix:
                continue  # removed from the result: not a line of the new file
            if prefix == "+" * width:
                out.append((line_no, row[width:]))
            line_no += 1
    return out


def _added_lines(source: _Source, path: str) -> list[tuple[int, str]]:
    """(line number, text) of every line ``source`` adds to ``path`` (all lines if read whole)."""
    if path in source.whole:
        try:
            text = (ROOT / path).read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            return []
        return list(enumerate(text.splitlines(), start=1))
    return _parse_added(_git(*source.diff_args, "--", path))


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
    if suffix in ARCHIVE_SUFFIXES and not lower.startswith(DATA_DIRS):
        return (
            f"{suffix} outside the synthetic-fixture folders — an archive or backup is "
            "almost always a real book; put a synthetic one under tests/fixtures/"
        )
    if (
        suffix == ".json"
        and BACKUP_JSON_NAME.search(Path(lower).name)
        and not lower.startswith(DATA_DIRS)
    ):
        return (
            "a JSON backup/export outside the synthetic-fixture folders — it is "
            "almost always a real book; put a synthetic one under tests/fixtures/"
        )
    if suffix in IMAGE_SUFFIXES and not lower.startswith(IMAGE_DIRS):
        return (
            "image outside the product-art folders — screenshots of a real book stay out; "
            "QA evidence belongs in a gitignored folder"
        )
    return None


def _scan_path(source: _Source, path: str, patterns: list[re.Pattern[str]]) -> list[str]:
    where = f"{source.label}:" if source.label else ""
    reason = check_path(path)
    if reason:
        return [f"{where}{path}: {reason}"]
    lines = _added_lines(source, path)
    findings: list[str] = []
    for line_no, text in lines:
        marked = ALLOW_MARKER in text
        if not marked and path not in EXEMPT_PATHS and LOCAL_PATH.search(text):
            findings.append(f"{where}{path}:{line_no}: a local home path names a real machine user")
        if marked and path in DENYLIST_MARKER_PATHS:
            continue
        folded = _fold(text)
        for i, pat in enumerate(patterns, start=1):
            if pat.search(folded):
                findings.append(f"{where}{path}:{line_no}: matches private denylist entry #{i}")
    if path.lower().endswith(".json") and not path.lower().startswith(DATA_DIRS):
        body = "\n".join(text for _, text in lines)
        if all(key in body for key in BACKUP_MANIFEST_KEYS):
            findings.append(f"{where}{path}: looks like a book export manifest (real data)")
    return findings


def scan(mode: str, rng: str | None = None, terms: list[str] | None = None) -> list[str]:
    patterns = _patterns(_denylist() if terms is None else terms)
    findings: list[str] = []
    for source in _sources(mode, rng):
        for path in source.names:
            findings += _scan_path(source, path, patterns)
    return list(dict.fromkeys(findings))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--staged", action="store_true", help="staged changes (pre-commit)")
    group.add_argument("--range", metavar="BASE..HEAD", help="every commit in a range (CI)")
    group.add_argument("--tree", action="store_true", help="every tracked file")
    group.add_argument(
        "--worktree", action="store_true", help="everything not yet in HEAD (agent hook)"
    )
    parser.add_argument(
        "--require-denylist",
        action="store_true",
        help="fail when no private denylist entries were loaded (CI on main, where the secret exists)",
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
    terms = _denylist()
    if not terms:
        if args.require_denylist:
            print(
                "privacy-guard: refused — 0 private denylist entries loaded, so the owner's "
                "identifiers were not checked (is the PRIVACY_DENYLIST secret set?)"
            )
            return 1
        print(
            f"privacy-guard: warning — no private denylist entries loaded (expected "
            f"{_denylist_path()}); only the file, path and secret rules ran",
            file=sys.stderr,
        )
    try:
        findings = scan(mode, args.range, terms)
    except (subprocess.CalledProcessError, ValueError) as exc:
        print(f"privacy-guard: could not read the changes to review: {exc}", file=sys.stderr)
        return 2
    if not findings:
        n = len(terms)
        print(f"privacy-guard: clean ({n} private denylist entr{'y' if n == 1 else 'ies'})")
        return 0
    print("privacy-guard: refused — this change may carry real personal or financial data:")
    for f in findings:
        print(f"  {f}")
    print(
        f"\nA synthetic home path that trips a rule: add `{ALLOW_MARKER}` on its line. The marker "
        "never silences a private-denylist match — replace that value with a synthetic one."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
