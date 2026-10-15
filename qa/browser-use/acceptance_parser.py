"""
acceptance_parser.py — parse GWT acceptance criteria from docs/qa/acceptance/*.md

Each .md file holds one or more acceptance criteria of the form:

    ### FEATURE-SHORT-ID
    - GIVEN ...
    - WHEN ...
    - THEN ...

This module turns those files into structured objects.  It has no app
dependencies — stdlib only.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

# The README has no criteria of its own; skip it.
_SKIP_FILES = {"README.md"}

# Regex for a criterion heading: ### SOME-ID  (with optional trailing text)
# The ID is the first word-group after ###, consisting of uppercase letters,
# digits, hyphens, and a single optional leading-arrow token (← …) which we strip.
_HEADING_RE = re.compile(r"^###\s+([A-Z][A-Z0-9-]*(?:-[A-Z0-9←][^\s]*)?)")

# Canonical GWT labels.  We accept any mix of whitespace + case.
_GWT_RE = re.compile(r"^-\s*(GIVEN|WHEN|THEN)\s+(.+)$", re.IGNORECASE)


@dataclass(frozen=True)
class AcceptanceCriterion:
    """One GIVEN/WHEN/THEN criterion parsed from the acceptance .md files."""

    id: str  # e.g. "ACCT-ADD-OPEN"
    feature: str  # e.g. "accounts"  (stem of the source file)
    given: str  # raw text of the GIVEN clause
    when: str  # raw text of the WHEN clause
    then: str  # raw text of the THEN clause
    source_file: Path


def _clean_id(raw: str) -> str:
    """Strip the regression annotation arrow + trailing commentary from an ID.

    E.g.  'ACCT-ADD-OPEN  ← regression guard'  →  'ACCT-ADD-OPEN'
    """
    # The arrow ← (U+2190) or plain '<' starts the commentary section.
    for sep in ("←", "<-", "←", " "):
        if sep in raw:
            raw = raw.split(sep)[0]
            break
    return raw.strip()


def _parse_file(path: Path) -> list[AcceptanceCriterion]:
    """Parse one acceptance .md file into a list of criteria."""
    feature = path.stem  # e.g. "accounts", "overview"
    lines = path.read_text(encoding="utf-8").splitlines()

    criteria: list[AcceptanceCriterion] = []
    current_id: str | None = None
    gwt: dict[str, str] = {}

    def _flush() -> None:
        if current_id is None:
            return
        # Only emit if we got at least one of GIVEN/WHEN/THEN
        if any(gwt.get(k) for k in ("given", "when", "then")):
            criteria.append(
                AcceptanceCriterion(
                    id=current_id,
                    feature=feature,
                    given=gwt.get("given", ""),
                    when=gwt.get("when", ""),
                    then=gwt.get("then", ""),
                    source_file=path,
                )
            )

    for line in lines:
        m = _HEADING_RE.match(line)
        if m:
            _flush()
            current_id = _clean_id(m.group(1))
            gwt = {}
            continue

        if current_id is None:
            continue

        g = _GWT_RE.match(line)
        if g:
            key = g.group(1).lower()  # "given" | "when" | "then"
            gwt[key] = g.group(2).strip()

    _flush()
    return criteria


def load_criteria(acceptance_dir: Path) -> list[AcceptanceCriterion]:
    """Load all criteria from every .md file in acceptance_dir.

    Files named README.md are skipped.  Files are processed in sorted order
    so the output is deterministic.
    """
    criteria: list[AcceptanceCriterion] = []
    for md_file in sorted(acceptance_dir.glob("*.md")):
        if md_file.name in _SKIP_FILES:
            continue
        criteria.extend(_parse_file(md_file))
    return criteria


def criteria_by_id(acceptance_dir: Path) -> dict[str, AcceptanceCriterion]:
    """Return a dict keyed by criterion ID (upper-cased)."""
    return {c.id.upper(): c for c in load_criteria(acceptance_dir)}
