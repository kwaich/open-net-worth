"""Tests for backend.evals.tool_surface.baseline — capture, persist, and run-over-run
comparison.

These are FAST and make NO LLM calls (mock persona only), same policy as
``test_tool_surface_harness.py``. What this protects:

* ``capture()`` produces a document shaped the way ``compare()`` expects to read it,
  and round-trips through ``save``/``load`` byte-for-byte.
* ``compare()`` renders the SAME verdict vocabulary as ``report._render_cost_axis``
  (BAD TRADE / REGRESSION / WIN) but across TWO RUNS rather than two arms in one run —
  that is the axis this module adds.
* a mock-captured side is always loudly flagged, never silently read as a real number.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.evals.tool_surface import baseline, tasks
from backend.evals.tool_surface.runner import make_mock_factory, run_one
from backend.evals.tool_surface.spec import ArmSpec


def _run(task_id: str, arm: str = "A", persona: str = "perfect"):
    task = tasks.by_id(task_id)
    assert task is not None, task_id
    spec = ArmSpec(key=arm, surface=arm)
    record = run_one(task, spec, 1, make_mock_factory(persona), decoys=[])
    record.arm = arm
    return record


# ── capture() shape ──────────────────────────────────────────────────────────


def test_capture_shape():
    record = _run("simple_expense")
    doc = baseline.capture([record], ["A"], {"model": "mock:perfect"}, mock=True)

    assert doc["mock"] is True
    assert doc["arms"] == ["A"]
    assert doc["meta"]["model"] == "mock:perfect"
    assert doc["git_commit"], "git_commit must never be empty"
    assert doc["summaries"]["A"]["runs"] == 1
    assert doc["summaries"]["A"]["success"]["mean"] == pytest.approx(1.0)
    assert doc["per_task_success"]["simple_expense"]["A"] == pytest.approx(1.0)
    assert doc["per_task_success"]["simple_expense"]["category"] == "ledger_simple"


def test_git_commit_and_branch_are_never_empty():
    assert baseline.git_commit()
    assert baseline.git_branch()


# ── save/load round-trip ─────────────────────────────────────────────────────


def test_save_and_load_round_trip(tmp_path):
    record = _run("simple_expense")
    doc = baseline.capture([record], ["A"], {}, mock=True)

    path = tmp_path / "nested" / "baseline.json"
    baseline.save(doc, path)
    assert path.exists()

    loaded = baseline.load(path)
    assert loaded == doc
    # Written as human-inspectable JSON, not a pickled blob.
    assert json.loads(path.read_text(encoding="utf-8"))["git_commit"] == doc["git_commit"]


# ── compare(): the verdict vocabulary, across TWO runs ───────────────────────


def test_compare_flags_bad_trade_cheaper_and_worse():
    good = _run("simple_expense")
    old_doc = baseline.capture([good], ["A"], {}, mock=True)

    worse = _run("simple_expense")
    worse.intended = False
    worse.cost.usd = good.cost.usd * 0.5
    new_doc = baseline.capture([worse], ["A"], {}, mock=True)

    rendered = baseline.compare(new_doc, old_doc)
    assert "BAD TRADE" in rendered


def test_compare_flags_regression_when_correctness_drops_alone():
    good = _run("simple_expense")
    old_doc = baseline.capture([good], ["A"], {}, mock=True)

    worse = _run("simple_expense")
    worse.intended = False
    new_doc = baseline.capture([worse], ["A"], {}, mock=True)

    rendered = baseline.compare(new_doc, old_doc)
    arm_row = next(line for line in rendered.splitlines() if line.startswith("A "))
    assert "REGRESSION" in arm_row
    assert "BAD TRADE" not in arm_row


def test_compare_flags_win_when_cheaper_and_not_worse():
    good = _run("simple_expense")
    old_doc = baseline.capture([good], ["A"], {}, mock=True)

    cheaper = _run("simple_expense")
    cheaper.cost.usd = good.cost.usd * 0.5
    new_doc = baseline.capture([cheaper], ["A"], {}, mock=True)

    rendered = baseline.compare(new_doc, old_doc)
    assert "WIN" in rendered


def test_compare_reports_missing_arm_instead_of_crashing():
    a_only = _run("simple_expense", arm="A")
    old_doc = baseline.capture([a_only], ["A"], {}, mock=True)

    b_only = _run("simple_expense", arm="B")
    new_doc = baseline.capture([b_only], ["B"], {}, mock=True)

    rendered = baseline.compare(new_doc, old_doc)
    assert "no baseline data" in rendered


def test_compare_names_per_task_regressions_individually():
    t1_old = _run("simple_expense")
    t2_old = _run("read_net_worth")
    old_doc = baseline.capture([t1_old, t2_old], ["A"], {}, mock=True)

    t1_new = _run("simple_expense")
    t1_new.intended = False  # this task regresses...
    t2_new = _run("read_net_worth")  # ...this one does not
    new_doc = baseline.capture([t1_new, t2_new], ["A"], {}, mock=True)

    rendered = baseline.compare(new_doc, old_doc)
    assert "simple_expense [A]: 100% -> 0%" in rendered
    assert "read_net_worth [A]" not in rendered


def test_compare_mock_banner_present_when_either_side_is_mock():
    good = _run("simple_expense")
    mock_doc = baseline.capture([good], ["A"], {}, mock=True)
    real_doc = dict(mock_doc)
    real_doc["mock"] = False

    assert "MOCK run" in baseline.compare(real_doc, mock_doc)
    assert "MOCK run" in baseline.compare(mock_doc, real_doc)


def test_compare_no_mock_banner_when_both_sides_are_real():
    good = _run("simple_expense")
    doc = baseline.capture([good], ["A"], {}, mock=False)
    assert "MOCK run" not in baseline.compare(doc, doc)


# ── CLI wiring: --save-baseline then --baseline, end to end ─────────────────


def test_cli_save_baseline_then_compare_against_it(tmp_path, capsys):
    from backend.evals.tool_surface.__main__ import main

    baseline_path = str(tmp_path / "baseline.json")

    rc = main(
        [
            "--mock",
            "--tasks",
            "simple_expense",
            "--repeats",
            "1",
            "--decoys",
            "0",
            "--save-baseline",
            baseline_path,
        ]
    )
    assert rc == 0
    assert Path(baseline_path).exists()
    capsys.readouterr()

    rc = main(
        [
            "--mock",
            "--tasks",
            "simple_expense",
            "--repeats",
            "1",
            "--decoys",
            "0",
            "--baseline",
            baseline_path,
        ]
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "VS STORED BASELINE" in out


def test_cli_baseline_flag_without_a_stored_file_does_not_crash(tmp_path, capsys):
    from backend.evals.tool_surface.__main__ import main

    missing = str(tmp_path / "does-not-exist.json")
    rc = main(["--mock", "--tasks", "simple_expense", "--repeats", "1", "--baseline", missing])
    assert rc == 0
    assert "NO BASELINE" in capsys.readouterr().out
