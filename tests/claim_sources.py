"""Where the model's machine-checkable CLAIMS live, and how to extract them.

A "claim" is any statement the model is TOLD that some code elsewhere has to honour:
a tool name it is instructed to call, a category/kind literal it is told to use, a
tool allowlist a stored playbook depends on. Every one of those can rot silently — the
prose keeps rendering, the model keeps reading it, and nothing fails.

This module is the shared extraction layer for ``tests/test_claim_contracts.py``,
``tests/test_claim_allowlists.py`` and the ``backend/scripts/audit_claims.py`` /
``prose_staleness.py`` diagnostics. It reads SOURCE TEXT (via ``ast``) rather than
calling the prompt builders, for three reasons: no live data dir is touched, the
extraction is deterministic, and every finding carries a real file:line the author can
open. Nothing here imports ``ai`` at module scope, so the reporting script can run
without booting the registry.
"""

from __future__ import annotations

import ast
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

PROMPTS_PY = REPO_ROOT / "backend" / "ai" / "prompts.py"
STATEMENT_BUNDLE_PY = REPO_ROOT / "backend" / "services" / "statement_import_bundle.py"
SKILL_CREATOR_BUNDLE_PY = REPO_ROOT / "backend" / "services" / "skill_creator_bundle.py"
SKILL_STORE_PY = REPO_ROOT / "backend" / "db" / "skill_store.py"
PLAYBOOK_PY = REPO_ROOT / "backend" / "services" / "_playbook.py"


@dataclass(frozen=True)
class Claim:
    """One piece of model-facing text, with the location an author can open."""

    source: str  # human label, e.g. "prompts.py" or "tool:add_transactions"
    path: str  # repo-relative file path ("" when the text came from the registry)
    line: int  # 1-based line, best-effort for registry-sourced text
    text: str

    def where(self) -> str:
        return f"{self.path}:{self.line}" if self.path else self.source


# ── Model-facing string extraction ────────────────────────────────────────────


def _docstring_ids(tree: ast.AST) -> set[int]:
    """Ids of the Constant nodes that are module/class/function DOCSTRINGS.

    Docstrings are developer commentary, not text the model ever sees. Excluding them is
    what keeps this extraction free of false positives: ``backend/ai/prompts.py``'s own
    docstrings discuss tool mechanics in prose that would otherwise look like claims.
    """
    out: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        body = getattr(node, "body", None) or []
        if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
            if isinstance(body[0].value.value, str):
                out.add(id(body[0].value))
    return out


def python_string_claims(path: Path, source_label: str | None = None) -> list[Claim]:
    """Every non-docstring string literal in a Python file, with its line number.

    f-string parts are individual ``Constant`` nodes, so an interpolated prompt yields
    its literal segments — which is exactly what we want to police.
    """
    src = path.read_text(encoding="utf-8")
    tree = ast.parse(src)
    skip = _docstring_ids(tree)
    label = source_label or path.name
    rel = str(path.relative_to(REPO_ROOT))
    out: list[Claim] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
            continue
        if id(node) in skip:
            continue
        out.append(Claim(label, rel, node.lineno, node.value))
    return out


def line_of(claim: Claim, token: str) -> int:
    """The real line of ``token`` inside a multi-line claim (prompts are one big literal)."""
    idx = claim.text.find(token)
    return claim.line + claim.text[:idx].count("\n") if idx >= 0 else claim.line


# ── Tool-name-shaped token detection ──────────────────────────────────────────

_SNAKE_TOKEN = re.compile(r"\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b")


def snake_tokens(text: str) -> set[str]:
    return {m.group(0) for m in _SNAKE_TOKEN.finditer(text)}


def tool_shaped_tokens(text: str, verbs: set[str]) -> set[str]:
    """Snake_case tokens that name a tool: first segment is a verb a REAL registered tool
    uses, OR the token is a KNOWN FORMER tool name.

    Anchoring the verb set on the live registry (rather than a hand-written verb list) is
    what keeps this self-updating: a new tool family teaches the detector its own prefix.
    Measured against the current tree this yields zero false positives on the prompt once
    the schema field vocabulary below is subtracted — see the module docstring of
    ``tests/test_claim_contracts.py`` for the numbers.

    The former-names half closes a blind spot that self-updating alone creates: when an
    ENTIRE verb family is retired, its verb leaves the live set and the detector stops
    recognising that family's dead names as tools at all. Retiring ``propose_*`` did exactly
    that on 2026-08-25 — the chat prompt kept telling the model to use
    ``propose_lot_dispose`` and this sweep could no longer see it. ``RENAMES`` is the record
    of every name that has ever been retired, so it is precisely the set prose must not use.
    """
    from backend.scripts.tool_renames import RENAMES

    return {t for t in snake_tokens(text) if t.split("_")[0] in verbs or t in RENAMES}


# ── Stored tool allowlists ────────────────────────────────────────────────────


# ── Merge-conflict markers ────────────────────────────────────────────────────

_CONFLICT = re.compile(r"^(?:<{7}|={7}|>{7})(?:\s|$)")

_SCANNED_SUFFIXES = {".py", ".md", ".ts", ".tsx", ".json", ".css", ".yml", ".yaml"}
_SKIP_DIRS = {
    ".git",
    ".venv",
    "node_modules",
    "dist",
    "build",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "htmlcov",
}


def _tracked_files() -> list[Path]:
    """Git-tracked files only, falling back to a pruned walk outside a checkout.

    Scoping to tracked files is both faster and MORE CORRECT than walking the tree: a
    conflict marker only matters if it is committed, and the repo carries a 335 MB
    ``node_modules`` plus build output that a full walk spends ~15s of CPU on. ``git
    ls-files`` answers in milliseconds.
    """
    try:
        out = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "ls-files", "-z"],
            capture_output=True,
            timeout=30,
        )
        if out.returncode == 0:
            names = out.stdout.decode("utf-8", "replace").split("\0")
            return [REPO_ROOT / n for n in names if n]
    except (subprocess.SubprocessError, OSError):
        pass
    return [
        p
        for p in REPO_ROOT.rglob("*")
        if p.is_file() and not any(part in _SKIP_DIRS for part in p.parts)
    ]


def files_with_conflict_markers() -> list[tuple[str, int, str]]:
    """``(relative path, line number, line)`` for every unresolved conflict marker."""
    out: list[tuple[str, int, str]] = []
    for path in _tracked_files():
        if path.suffix not in _SCANNED_SUFFIXES:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError, FileNotFoundError):
            continue
        # Fast reject. The '=======' rule alone is a markdown false positive (setext
        # headings), so a marker only counts when a real <<<<<<< / >>>>>>> is present.
        if "<<<<<<<" not in text and ">>>>>>>" not in text:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            if _CONFLICT.match(line):
                out.append((str(path.relative_to(REPO_ROOT)), i, line[:60]))
    return out
