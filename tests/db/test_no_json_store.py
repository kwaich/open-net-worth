"""Forever-guard: JsonStore is retired (plan 4e, task 4 — the terminal deletion of the
4-plan storage migration). This test fails the instant anyone reintroduces the pattern
it retired.

``backend/db/json_store.py`` is deleted; ``PayloadTableStore``/DB (``backend/db/
payload_store.py``, ``backend/db/tables.py``, ``backend/db/engine.py``) is now the only
store substrate — see ``docs/adr/2026-08-09-json-store-retired.md``. Three independent
checks, each walking ``backend/`` FRESH on every run — never a cached list, so a file
added the day after this test was last touched is still covered by the very next run:

  (a) the file itself does not exist;
  (b) no class under ``backend/`` subclasses ``JsonStore`` (an AST ``ClassDef.bases``
      scan, not a grep — a subclass importing it under an alias would slip past a plain
      textual search but not this);
  (c) no import statement anywhere under ``backend/`` reaches ``backend.db.json_store``.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
BACKEND = REPO / "backend"
JSON_STORE_PATH = BACKEND / "db" / "json_store.py"

# Superset of the three patterns the retirement task itself was gated on
# (``^from .*json_store import``, ``^from backend\.db import json_store``,
# ``^from backend\.db\.json_store``) plus a bare ``import backend.db.json_store`` —
# a forever-guard must be at least as strict as everything already verified once.
_IMPORT_LINE_RE = re.compile(r"^\s*(from|import)\s+.*json_store")


def _backend_sources() -> list[Path]:
    """Fresh directory walk, never cached — a file that didn't exist when this test
    was written must still be covered the first time it does."""
    return sorted(BACKEND.rglob("*.py"))


def _base_name(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def test_json_store_file_does_not_exist():
    assert not JSON_STORE_PATH.exists(), (
        f"{JSON_STORE_PATH.relative_to(REPO)} exists — JsonStore was retired (plan 4e); "
        "it must not be reintroduced. New stores are PayloadTableStore (DB-backed); "
        "see docs/adr/2026-08-09-json-store-retired.md."
    )


def test_no_class_under_backend_subclasses_json_store():
    hits: list[str] = []
    for path in _backend_sources():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError:  # pragma: no cover - a parse failure is its own test's problem
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and any(
                _base_name(base) == "JsonStore" for base in node.bases
            ):
                hits.append(f"{path.relative_to(REPO)}:{node.lineno} class {node.name}")
    assert not hits, (
        "found a class subclassing JsonStore under backend/ — JsonStore is retired; "
        "use PayloadTableStore instead (see the add-store skill):\n" + "\n".join(hits)
    )


def test_no_import_statement_reaches_json_store_under_backend():
    hits: list[str] = []
    for path in _backend_sources():
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if _IMPORT_LINE_RE.match(line):
                hits.append(f"{path.relative_to(REPO)}:{lineno}: {line.strip()}")
    assert not hits, (
        "found an import statement reaching json_store under backend/ — the module "
        "backend/db/json_store.py is deleted:\n" + "\n".join(hits)
    )
