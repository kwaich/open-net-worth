"""scripts/gate_baseline.py — the release gate fails on new failures only."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location(
    "gate_baseline", ROOT / "scripts" / "gate_baseline.py"
)
gb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gb)

JUNIT = """<testsuites><testsuite>
<testcase classname="tests.test_a" name="test_ok"/>
<testcase classname="tests.test_a" name="test_known"><failure message="x"/></testcase>
<testcase classname="tests.test_b" name="test_new"><error message="boom"/></testcase>
<testcase classname="tests.test_b" name="test_skip"><skipped/></testcase>
</testsuite></testsuites>"""


def _junit(tmp_path: Path) -> Path:
    path = tmp_path / "junit.xml"
    path.write_text(JUNIT)
    return path


def test_new_failure_fails_the_gate_and_known_one_does_not(tmp_path, capsys):
    baseline = tmp_path / "known.txt"
    baseline.write_text("# comment\ntests.test_a::test_known\n")
    assert gb.main(["pytest", str(_junit(tmp_path)), str(baseline)]) == 1
    err = capsys.readouterr().err
    assert "NEW FAILURE: tests.test_b::test_new" in err
    assert "test_known" not in err


def test_only_known_failures_passes_and_reports_stale_entries(tmp_path, capsys):
    baseline = tmp_path / "known.txt"
    baseline.write_text(
        "tests.test_a::test_known\ntests.test_b::test_new\ntests.test_c::test_fixed\n"
    )
    assert gb.main(["pytest", str(_junit(tmp_path)), str(baseline)]) == 0
    assert "stale baseline entry (now passes): tests.test_c::test_fixed" in capsys.readouterr().out


def test_write_regenerates_the_baseline_sorted(tmp_path):
    baseline = tmp_path / "known.txt"
    assert gb.main(["pytest", str(_junit(tmp_path)), str(baseline), "--write"]) == 0
    ids = [line for line in baseline.read_text().splitlines() if not line.startswith("#")]
    assert ids == ["tests.test_a::test_known", "tests.test_b::test_new"]


def test_an_empty_report_is_not_a_pass(tmp_path):
    empty = tmp_path / "junit.xml"
    empty.write_text("<testsuites/>")
    assert gb.main(["pytest", str(empty), str(tmp_path / "known.txt")]) == 1


def test_vitest_report_ids_are_relative_and_suite_crashes_count(tmp_path, capsys):
    report = tmp_path / "vitest.json"
    report.write_text(
        json.dumps(
            {
                "testResults": [
                    {
                        "name": "/runner/src/frontend/src/a.test.ts",
                        "status": "failed",
                        "assertionResults": [
                            {"status": "passed", "fullName": "a ok"},
                            {"status": "failed", "fullName": "a breaks"},
                        ],
                    },
                    {
                        "name": "/runner/src/frontend/src/b.test.ts",
                        "status": "failed",
                        "assertionResults": [],
                    },
                ]
            }
        )
    )
    baseline = tmp_path / "known.txt"
    baseline.write_text("src/a.test.ts::a breaks\n")
    assert gb.main(["vitest", str(report), str(baseline)]) == 1
    assert "NEW FAILURE: src/b.test.ts::<suite failed to run>" in capsys.readouterr().err
