#!/usr/bin/env python3
"""
acceptance_check.py — coverage report linking GWT criteria ↔ browser-use specs.

Each spec YAML may declare:

    acceptance_ids:
      - ACCT-LOAD
      - ACCT-ADD-OPEN

This script:
  1. Loads all criteria from docs/qa/acceptance/*.md via acceptance_parser.
  2. Loads all specs from qa/browser-use/specs/*.yaml.
  3. Reports which criteria are COVERED (≥1 spec references them) and which
     are UNCOVERED, grouped by feature.
  4. Exits 0 always (this is an informational report, not a gate).

Usage:
    python3 acceptance_check.py [--acceptance-dir PATH] [--specs-dir PATH]
                                [--format text|json] [--summary-only]

The script uses only stdlib + pyyaml (already in requirements.txt).  It does
NOT import anything from the app backend.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import yaml

# ---- project-relative defaults -----
_HERE = Path(__file__).parent
_REPO_ROOT = _HERE.parent.parent  # qa/browser-use/../../  = project root
_DEFAULT_ACCEPTANCE_DIR = _REPO_ROOT / "docs" / "qa" / "acceptance"
_DEFAULT_SPECS_DIR = _HERE / "specs"


# ---------------------------------------------------------------------------
# Spec loader (minimal — no browser-use import needed)
# ---------------------------------------------------------------------------


def _load_spec_acceptance_ids(specs_dir: Path) -> dict[str, list[str]]:
    """Return {spec_id: [acceptance_criterion_id, ...]} for all YAML specs."""
    result: dict[str, list[str]] = {}
    for yaml_file in sorted(specs_dir.glob("*.yaml")):
        with yaml_file.open() as fh:
            raw = yaml.safe_load(fh)
        if not isinstance(raw, dict):
            continue
        spec_id: str = raw.get("id", yaml_file.stem)
        ids: list[str] = raw.get("acceptance_ids", []) or []
        result[spec_id] = [i.upper().strip() for i in ids]
    return result


# ---------------------------------------------------------------------------
# Coverage computation
# ---------------------------------------------------------------------------


def compute_coverage(
    criteria_by_id: dict[str, Any],
    spec_acceptance_ids: dict[str, list[str]],
) -> dict[str, Any]:
    """Return a coverage summary dict."""
    # Build inverse map: criterion_id → [spec_ids that cover it]
    covered_by: dict[str, list[str]] = {cid: [] for cid in criteria_by_id}
    for spec_id, cids in spec_acceptance_ids.items():
        for cid in cids:
            if cid in covered_by:
                covered_by[cid].append(spec_id)
            # else: spec references an unknown criterion — warn later

    # Unknown criterion references in specs
    all_known = set(criteria_by_id.keys())
    unknown_refs: dict[str, list[str]] = {}
    for spec_id, cids in spec_acceptance_ids.items():
        bad = [c for c in cids if c not in all_known]
        if bad:
            unknown_refs[spec_id] = bad

    covered_ids = sorted(cid for cid, specs in covered_by.items() if specs)
    uncovered_ids = sorted(cid for cid, specs in covered_by.items() if not specs)

    total = len(covered_by)
    n_covered = len(covered_ids)
    pct = round(100.0 * n_covered / total, 1) if total else 0.0

    # Group by feature
    def _feature(cid: str) -> str:
        c = criteria_by_id.get(cid)
        return c.feature if c else "unknown"

    features: dict[str, dict[str, Any]] = {}
    for cid in sorted(covered_by):
        feat = _feature(cid)
        if feat not in features:
            features[feat] = {"covered": [], "uncovered": []}
        if covered_by[cid]:
            features[feat]["covered"].append(cid)
        else:
            features[feat]["uncovered"].append(cid)

    return {
        "total": total,
        "covered": n_covered,
        "uncovered": total - n_covered,
        "coverage_pct": pct,
        "covered_ids": covered_ids,
        "uncovered_ids": uncovered_ids,
        "covered_by": {k: v for k, v in covered_by.items() if v},
        "features": features,
        "unknown_refs": unknown_refs,
    }


# ---------------------------------------------------------------------------
# Text reporter
# ---------------------------------------------------------------------------


def _print_text_report(cov: dict[str, Any], summary_only: bool = False) -> None:
    total = cov["total"]
    n_covered = cov["covered"]
    n_uncovered = cov["uncovered"]
    pct = cov["coverage_pct"]
    unknown_refs = cov["unknown_refs"]

    print()
    print("=" * 70)
    print("  Open Net Worth — QA Acceptance Coverage Report")
    print("=" * 70)
    print(
        f"\n  Criteria total : {total}"
        f"\n  Covered        : {n_covered}"
        f"\n  Uncovered      : {n_uncovered}"
        f"\n  Coverage       : {pct}%"
    )

    if unknown_refs:
        print("\n  [!] UNKNOWN criterion IDs referenced in specs:")
        for spec_id, bad_ids in sorted(unknown_refs.items()):
            print(f"      {spec_id}: {', '.join(bad_ids)}")

    if summary_only:
        print()
        return

    # Per-feature breakdown
    features = cov["features"]
    for feat in sorted(features):
        feat_data = features[feat]
        n_f_cov = len(feat_data["covered"])
        n_f_unc = len(feat_data["uncovered"])
        n_f_tot = n_f_cov + n_f_unc
        f_pct = round(100.0 * n_f_cov / n_f_tot, 0) if n_f_tot else 0
        marker = "OK" if n_f_unc == 0 else "  "
        print(f"\n  [{marker}] {feat.upper():<20} {n_f_cov}/{n_f_tot} ({f_pct:.0f}%)")

        for cid in feat_data["covered"]:
            spec_list = ", ".join(cov["covered_by"].get(cid, []))
            print(f"        [+] {cid:<40}  <- {spec_list}")
        for cid in feat_data["uncovered"]:
            print(f"        [ ] {cid}")

    print()
    print("  Legend: [+] covered  [ ] uncovered")
    print()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Report coverage: GWT acceptance criteria ↔ browser-use specs."
    )
    p.add_argument(
        "--acceptance-dir",
        type=Path,
        default=_DEFAULT_ACCEPTANCE_DIR,
        help=f"Directory containing acceptance *.md files (default: {_DEFAULT_ACCEPTANCE_DIR})",
    )
    p.add_argument(
        "--specs-dir",
        type=Path,
        default=_DEFAULT_SPECS_DIR,
        help=f"Directory containing spec *.yaml files (default: {_DEFAULT_SPECS_DIR})",
    )
    p.add_argument(
        "--format",
        choices=["text", "json"],
        default="text",
        help="Output format (default: text)",
    )
    p.add_argument(
        "--summary-only",
        action="store_true",
        help="Print totals only, not per-criterion detail",
    )
    return p.parse_args()


def main() -> int:
    args = _parse_args()

    # Import here so this script can run with stdlib alone when called with
    # --help, and only pulls in the parser when actually running.
    # acceptance_parser lives in the same directory.
    sys.path.insert(0, str(Path(__file__).parent))
    from acceptance_parser import criteria_by_id as _criteria_by_id

    if not args.acceptance_dir.is_dir():
        print(
            f"ERROR: acceptance_dir not found: {args.acceptance_dir}",
            file=sys.stderr,
        )
        return 2

    if not args.specs_dir.is_dir():
        print(
            f"ERROR: specs_dir not found: {args.specs_dir}",
            file=sys.stderr,
        )
        return 2

    criteria = _criteria_by_id(args.acceptance_dir)
    spec_ids = _load_spec_acceptance_ids(args.specs_dir)
    cov = compute_coverage(criteria, spec_ids)

    if args.format == "json":
        print(json.dumps(cov, indent=2))
    else:
        _print_text_report(cov, summary_only=args.summary_only)

    return 0


if __name__ == "__main__":
    sys.exit(main())
