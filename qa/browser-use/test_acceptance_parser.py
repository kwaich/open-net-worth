"""
Unit tests for acceptance_parser.py.

These tests use only stdlib + the parser module — no app imports, no browser-use,
no YAML deps.  Run with:

    python3 -m pytest qa/browser-use/test_acceptance_parser.py -v

or (from the qa/browser-use directory):

    python3 -m pytest test_acceptance_parser.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Allow importing acceptance_parser from the same directory when running pytest
# from the repo root (where sys.path may not include qa/browser-use/).
sys.path.insert(0, str(Path(__file__).parent))

from acceptance_parser import (
    AcceptanceCriterion,
    _clean_id,
    _parse_file,
    criteria_by_id,
    load_criteria,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_md(directory: Path, name: str, content: str) -> Path:
    """Write a .md file to directory and return its path."""
    p = directory / name
    p.write_text(content, encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# _clean_id
# ---------------------------------------------------------------------------


class TestCleanId:
    def test_plain_id(self) -> None:
        assert _clean_id("ACCT-ADD-OPEN") == "ACCT-ADD-OPEN"

    def test_strips_arrow_annotation(self) -> None:
        # The accounts.md file has "ACCT-ADD-OPEN  ← regression guard for the fixed bug"
        assert _clean_id("ACCT-ADD-OPEN  ← regression guard") == "ACCT-ADD-OPEN"

    def test_strips_trailing_whitespace(self) -> None:
        assert _clean_id("ACCT-LOAD   ") == "ACCT-LOAD"

    def test_strips_ascii_arrow(self) -> None:
        assert _clean_id("FOO-BAR <- some note") == "FOO-BAR"

    def test_no_annotation(self) -> None:
        assert _clean_id("PLAN-DRAG-REORDER-WITHIN-GROUP") == "PLAN-DRAG-REORDER-WITHIN-GROUP"


# ---------------------------------------------------------------------------
# _parse_file
# ---------------------------------------------------------------------------


class TestParseFile:
    def test_basic_gwt(self, tmp_path: Path) -> None:
        md = _write_md(
            tmp_path,
            "test.md",
            """
### ACCT-LOAD
- GIVEN I am on any tab
- WHEN I click "Accounts" in the sidebar
- THEN the Accounts tab loads showing the account list
""",
        )
        results = _parse_file(md)
        assert len(results) == 1
        c = results[0]
        assert c.id == "ACCT-LOAD"
        assert c.feature == "test"
        assert "any tab" in c.given
        assert "Accounts" in c.when
        assert "account list" in c.then

    def test_multiple_criteria(self, tmp_path: Path) -> None:
        md = _write_md(
            tmp_path,
            "multi.md",
            """
### FOO-ONE
- GIVEN state A
- WHEN action B
- THEN result C

### FOO-TWO
- GIVEN state X
- WHEN action Y
- THEN result Z
""",
        )
        results = _parse_file(md)
        assert len(results) == 2
        assert results[0].id == "FOO-ONE"
        assert results[1].id == "FOO-TWO"

    def test_strips_arrow_annotation_from_id(self, tmp_path: Path) -> None:
        md = _write_md(
            tmp_path,
            "regression.md",
            """
### ACCT-ADD-OPEN  ← regression guard for the fixed bug
- GIVEN I am on the Accounts tab
- WHEN I click "Configure rates"
- THEN the modal opens
""",
        )
        results = _parse_file(md)
        assert len(results) == 1
        assert results[0].id == "ACCT-ADD-OPEN"

    def test_feature_name_from_filename(self, tmp_path: Path) -> None:
        md = _write_md(
            tmp_path,
            "accounts.md",
            """
### ACCT-LOAD
- GIVEN I am on any tab
- WHEN I click "Accounts"
- THEN the Accounts tab renders
""",
        )
        results = _parse_file(md)
        assert results[0].feature == "accounts"

    def test_skips_heading_without_gwt(self, tmp_path: Path) -> None:
        # A heading with no GIVEN/WHEN/THEN lines should be silently skipped.
        md = _write_md(
            tmp_path,
            "empty.md",
            """
### EMPTY-CRIT
Just some prose, no GWT lines.
""",
        )
        results = _parse_file(md)
        assert results == []

    def test_case_insensitive_gwt_labels(self, tmp_path: Path) -> None:
        md = _write_md(
            tmp_path,
            "ci.md",
            """
### CI-TEST
- given something
- when action
- then outcome
""",
        )
        results = _parse_file(md)
        assert len(results) == 1
        assert results[0].given == "something"

    def test_partial_gwt_still_parsed(self, tmp_path: Path) -> None:
        # Only GIVEN + WHEN, no THEN — still emitted with empty then.
        md = _write_md(
            tmp_path,
            "partial.md",
            """
### PART-ONE
- GIVEN state
- WHEN action
""",
        )
        results = _parse_file(md)
        assert len(results) == 1
        assert results[0].then == ""

    def test_real_acceptance_file_roundtrip(self) -> None:
        """Parse the actual accounts.md acceptance file and verify known IDs are present."""
        accounts_md = (
            Path(__file__).parent.parent.parent  # qa/browser-use/../../../
            / "docs"
            / "qa"
            / "acceptance"
            / "accounts.md"
        )
        if not accounts_md.exists():
            pytest.skip("docs/qa/acceptance/accounts.md not found")
        results = _parse_file(accounts_md)
        ids = {c.id for c in results}
        assert "ACCT-LOAD" in ids
        assert "ACCT-SELECT" in ids
        assert "ACCT-ADD-OPEN" in ids
        assert "ACCT-ADD-SAVE" in ids
        assert "ACCT-ADD-TXN" in ids
        assert len(results) == 28  # accounts.md currently defines 28 criteria


# ---------------------------------------------------------------------------
# load_criteria / criteria_by_id
# ---------------------------------------------------------------------------


class TestLoadCriteria:
    def test_loads_multiple_files(self, tmp_path: Path) -> None:
        _write_md(
            tmp_path,
            "accounts.md",
            """
### ACCT-LOAD
- GIVEN I am anywhere
- WHEN I click Accounts
- THEN Accounts renders
""",
        )
        _write_md(
            tmp_path,
            "budgeting.md",
            """
### BUD-LOAD
- GIVEN I am anywhere
- WHEN I click Plan
- THEN Plan renders
""",
        )
        results = load_criteria(tmp_path)
        ids = {c.id for c in results}
        assert "ACCT-LOAD" in ids
        assert "BUD-LOAD" in ids

    def test_skips_readme(self, tmp_path: Path) -> None:
        _write_md(
            tmp_path,
            "README.md",
            """
### README-HEADING
- GIVEN anything
- WHEN anything
- THEN anything
""",
        )
        _write_md(
            tmp_path,
            "real.md",
            """
### REAL-CRIT
- GIVEN state
- WHEN action
- THEN outcome
""",
        )
        results = load_criteria(tmp_path)
        ids = {c.id for c in results}
        assert "README-HEADING" not in ids
        assert "REAL-CRIT" in ids

    def test_deterministic_order(self, tmp_path: Path) -> None:
        # Files are processed in sorted order; criteria should be stable.
        _write_md(tmp_path, "z.md", "### Z-CRIT\n- GIVEN a\n- WHEN b\n- THEN c\n")
        _write_md(tmp_path, "a.md", "### A-CRIT\n- GIVEN a\n- WHEN b\n- THEN c\n")
        results = load_criteria(tmp_path)
        assert results[0].id == "A-CRIT"
        assert results[1].id == "Z-CRIT"

    def test_criteria_by_id_keyed_uppercase(self, tmp_path: Path) -> None:
        _write_md(tmp_path, "feat.md", "### FEAT-ONE\n- GIVEN a\n- WHEN b\n- THEN c\n")
        by_id = criteria_by_id(tmp_path)
        assert "FEAT-ONE" in by_id
        assert isinstance(by_id["FEAT-ONE"], AcceptanceCriterion)

    def test_real_acceptance_dir(self) -> None:
        """Load the real acceptance directory and verify the total count."""
        acceptance_dir = Path(__file__).parent.parent.parent / "docs" / "qa" / "acceptance"
        if not acceptance_dir.is_dir():
            pytest.skip("docs/qa/acceptance/ not found")
        criteria = load_criteria(acceptance_dir)
        # We have 14 feature files × ~10 criteria each.  As of this commit
        # the total is at least 100 and the known features are all present.
        assert len(criteria) >= 100
        ids = {c.id for c in criteria}
        # Spot-check a sampling across features
        for expected_id in (
            "ACCT-LOAD",
            "OV-HERO-STATS",
            "ACCT-ADD-SAVE",
            "PLAN-DRAG-REORDER-WITHIN-GROUP",
            "CHAT-SEND-PLAIN",
            "CONN-LOAD",
            "SETTINGS-LOAD",
            "SKILLS-LOAD",
            "INBOX-LOAD",
        ):
            assert expected_id in ids, f"Expected criterion {expected_id!r} not found"


# ---------------------------------------------------------------------------
# Integration: acceptance_check coverage function
# ---------------------------------------------------------------------------


class TestCoverageComputation:
    """Verify acceptance_check.compute_coverage logic without needing the app."""

    def setup_method(self) -> None:
        sys.path.insert(0, str(Path(__file__).parent))
        from acceptance_check import compute_coverage

        self._compute = compute_coverage

    def _make_criteria(self, ids: list[str]) -> dict:
        """Build a minimal criteria_by_id dict."""

        class _FakeCriterion:
            def __init__(self, cid: str) -> None:
                self.id = cid
                self.feature = cid.split("-")[0].lower()

        return {cid: _FakeCriterion(cid) for cid in ids}

    def test_fully_covered(self) -> None:
        criteria = self._make_criteria(["A-ONE", "A-TWO"])
        spec_ids = {"spec-1": ["A-ONE", "A-TWO"]}
        cov = self._compute(criteria, spec_ids)
        assert cov["covered"] == 2
        assert cov["uncovered"] == 0
        assert cov["coverage_pct"] == 100.0

    def test_fully_uncovered(self) -> None:
        criteria = self._make_criteria(["A-ONE", "A-TWO"])
        cov = self._compute(criteria, {})
        assert cov["covered"] == 0
        assert cov["uncovered"] == 2

    def test_partial_coverage(self) -> None:
        criteria = self._make_criteria(["A-ONE", "A-TWO", "A-THREE"])
        spec_ids = {"spec-1": ["A-ONE"]}
        cov = self._compute(criteria, spec_ids)
        assert cov["covered"] == 1
        assert cov["uncovered"] == 2
        assert cov["coverage_pct"] == pytest.approx(33.3, abs=0.1)

    def test_unknown_ref_detected(self) -> None:
        criteria = self._make_criteria(["A-ONE"])
        spec_ids = {"spec-x": ["A-ONE", "DOES-NOT-EXIST"]}
        cov = self._compute(criteria, spec_ids)
        assert "spec-x" in cov["unknown_refs"]
        assert "DOES-NOT-EXIST" in cov["unknown_refs"]["spec-x"]

    def test_multiple_specs_same_criterion(self) -> None:
        criteria = self._make_criteria(["A-ONE"])
        spec_ids = {"spec-1": ["A-ONE"], "spec-2": ["A-ONE"]}
        cov = self._compute(criteria, spec_ids)
        assert cov["covered"] == 1
        assert set(cov["covered_by"]["A-ONE"]) == {"spec-1", "spec-2"}

    def test_empty_inputs(self) -> None:
        cov = self._compute({}, {})
        assert cov["total"] == 0
        assert cov["coverage_pct"] == 0.0
