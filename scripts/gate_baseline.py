"""Release gate that fails only on NEW test failures.

The suites carry a known set of failures (tests/known_failures.txt, frontend/known_failures.txt)
that predates the release process. The gate must still stop a release for anything that breaks
after that baseline was taken, so it compares the failed test ids of a run with the committed list:

    python3 scripts/gate_baseline.py pytest   junit.xml          tests/known_failures.txt
    python3 scripts/gate_baseline.py vitest   vitest-report.json frontend/known_failures.txt
    python3 scripts/gate_baseline.py pytest   junit.xml          tests/known_failures.txt --write

Exit 0: no failure outside the baseline. Exit 1: at least one new failure, or a report with no
tests in it (a suite that did not run is not a pass). Baseline entries that now pass are listed as
stale so the file only ever shrinks: `--write` regenerates it from the run. Output names test ids
only, never output or source of a test.
"""

from __future__ import annotations

import argparse
import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


def pytest_run(report: Path) -> tuple[int, set[str]]:
    root = ET.parse(report).getroot()
    total, failed = 0, set()
    for case in root.iter("testcase"):
        if case.find("skipped") is not None:
            continue
        total += 1
        if case.find("failure") is not None or case.find("error") is not None:
            failed.add(f"{case.get('classname', '')}::{case.get('name', '')}")
    return total, failed


def vitest_run(report: Path) -> tuple[int, set[str]]:
    data = json.loads(report.read_text())
    total, failed = 0, set()
    for suite in data.get("testResults", []):
        name = _relative(suite.get("name", ""))
        assertions = suite.get("assertionResults", [])
        if suite.get("status") == "failed" and not assertions:
            failed.add(f"{name}::<suite failed to run>")
        for test in assertions:
            if test.get("status") in ("pending", "skipped", "todo"):
                continue
            total += 1
            if test.get("status") == "failed":
                failed.add(f"{name}::{test.get('fullName', test.get('title', ''))}")
    return total, failed


def _relative(path: str) -> str:
    marker = "/frontend/"
    return path.split(marker, 1)[1] if marker in path else path


def read_baseline(path: Path) -> set[str]:
    if not path.exists():
        return set()
    lines = (line.strip() for line in path.read_text().splitlines())
    return {line for line in lines if line and not line.startswith("#")}


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("kind", choices=("pytest", "vitest"))
    parser.add_argument("report", type=Path)
    parser.add_argument("baseline", type=Path)
    parser.add_argument(
        "--write", action="store_true", help="regenerate the baseline from this run"
    )
    args = parser.parse_args(argv)

    total, failed = (pytest_run if args.kind == "pytest" else vitest_run)(args.report)
    if total == 0:
        sys.stderr.write(
            f"ERROR: {args.kind} report has no executed tests - the suite did not run\n"
        )
        return 1
    if args.write:
        header = "# Known failures that predate the release gate. Shrinks only; see scripts/gate_baseline.py.\n"
        args.baseline.write_text(header + "".join(f"{t}\n" for t in sorted(failed)))
        sys.stdout.write(f"{args.kind}: wrote {len(failed)} known failures of {total} tests\n")
        return 0

    baseline = read_baseline(args.baseline)
    new = sorted(failed - baseline)
    stale = sorted(baseline - failed)
    sys.stdout.write(
        f"{args.kind}: {total} tests, {len(failed)} failed, {len(failed & baseline)} known, "
        f"{len(new)} new, {len(stale)} baseline entries now pass\n"
    )
    for test in stale[:20]:
        sys.stdout.write(f"  stale baseline entry (now passes): {test}\n")
    for test in new:
        sys.stderr.write(f"NEW FAILURE: {test}\n")
    return 1 if new else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
