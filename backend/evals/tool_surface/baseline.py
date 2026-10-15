"""Persist a run's summary and compare a LATER run against it — run-over-run, not
arm-vs-arm.

``report._render_cost_axis`` already compares arms WITHIN one run (e.g. A vs B vs C,
side by side). That answers "which surface is better today." It does NOT answer the
question this module exists for: **did tonight's change move cost and correctness, and
in which direction, relative to how the SAME arm behaved before the change landed.**

That is the measurement the reliability program's control loop depends on ("nothing
ships that moves one number the right way and another the wrong way" —
``.claude/rfcs/harness-audit-2026-07-18/SYSTEM.md``). Five lanes are shipping changes to
the AI harness the same night this module was written; without a captured "before"
number, none of their claims of improvement are checkable — they are five more
confident assertions, which is exactly the failure this program exists to close.

A captured baseline is a small, self-contained JSON document: per-arm summary stats
(flattened ``report.Stat``s), a per-task success grid, and enough provenance (git commit
+ branch + timestamp + whether it was a mock run) that a reader six months from now can
tell what it measured and whether the commit it was taken at still means anything.

**Honesty rule, enforced in the rendering, not just the docstring:** a mock-captured
baseline has a CONSTANT correctness axis by construction (the scripted persona behaves
identically regardless of what changed) and an ESTIMATED cost axis (~4 chars/token, not
API-measured). ``compare()`` stamps every mock side of a comparison with a loud banner
and never lets a mock-vs-mock or mock-vs-real comparison masquerade as a verdict.
"""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backend.evals.tool_surface.report import Stat, per_task_grid, summarise_arm
from backend.evals.tool_surface.runner import RunRecord

#: A single-task drop larger than this many percentage points is named individually in
#: the comparison output, not left to drown in the aggregate.
_TASK_REGRESSION_THRESHOLD_PP = 20.0


def git_commit() -> str:
    return _git(["rev-parse", "HEAD"])


def git_branch() -> str:
    return _git(["rev-parse", "--abbrev-ref", "HEAD"])


def _git(args: list[str]) -> str:
    try:
        out = subprocess.run(["git", *args], capture_output=True, text=True, timeout=5, check=True)
        return out.stdout.strip() or "unknown"
    except Exception:  # noqa: BLE001 - provenance is best-effort, never fatal
        return "unknown"


def _stat_dict(s: Stat) -> dict:
    return {"mean": s.mean, "stddev": s.stddev, "n": s.n}


def _summary_dict(arm: str, records: list[RunRecord]) -> dict:
    s = summarise_arm(arm, records)
    return {
        "arm": s.arm,
        "label": s.label,
        "runs": s.runs,
        "estimated": s.estimated,
        "success": _stat_dict(s.success),
        "intended": _stat_dict(s.intended),
        "unintended": _stat_dict(s.unintended),
        "wrong_value": _stat_dict(s.wrong_value),
        "selection": _stat_dict(s.selection),
        "round_trips": _stat_dict(s.round_trips),
        "tool_calls": _stat_dict(s.tool_calls),
        "discovery": _stat_dict(s.discovery),
        "errors": _stat_dict(s.errors),
        "recovered": _stat_dict(s.recovered),
        "input_tokens": _stat_dict(s.input_tokens),
        "output_tokens": _stat_dict(s.output_tokens),
        "wall": _stat_dict(s.wall),
        "api_calls": _stat_dict(s.api_calls),
        "uncached_in": _stat_dict(s.uncached_in),
        "cache_write": _stat_dict(s.cache_write),
        "cache_read": _stat_dict(s.cache_read),
        "cost_per_run": _stat_dict(s.cost_per_run),
        "cost_per_success": s.cost_per_success,
    }


def capture(records: list[RunRecord], arms: list[str], meta: dict[str, Any], *, mock: bool) -> dict:
    """Build the persistable document for THIS run. Pure — writes nothing."""
    grid = per_task_grid(records, arms)
    per_task: dict[str, dict[str, Any]] = {}
    for row in grid:
        per_task[row["task"]] = {
            "category": row["category"],
            **{arm: (row[arm].mean if row.get(arm) is not None else None) for arm in arms},
        }
    full_meta = dict(meta)
    if mock:
        # Structural, not just prose: this key exists on EVERY mock-captured document,
        # so a reader (or `compare()`) can never encounter one without the caveat baked
        # in — the coordinator's finding that a mock's correctness axis is constant by
        # construction, and its dollars are ~4-chars/token estimates, must survive
        # copy-pasting just the JSON with no README nearby.
        full_meta["MOCK_CAPTURE_WARNING"] = (
            "This document's correctness numbers are NOT measured — the scripted "
            "persona behaves identically regardless of what changed, so every "
            "per_task_success value is an artifact of the mock, not a finding about "
            "the real assistant. Cost figures are ~4-chars/token ESTIMATES, not "
            "API-measured. Use only to prove the capture/compare mechanism works or to "
            "pin the task-set/fixture shape at a commit — never as evidence the "
            "assistant is fast, cheap, or correct."
        )
    return {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "git_commit": git_commit(),
        "git_branch": git_branch(),
        "mock": mock,
        "meta": full_meta,
        "arms": list(arms),
        "summaries": {arm: _summary_dict(arm, records) for arm in arms},
        "per_task_success": per_task,
    }


def save(doc: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _pct_delta(new: float, old: float) -> float:
    if not old:
        return 0.0
    return (new - old) / old * 100.0


def _mock_banner(current: dict, baseline: dict) -> list[str]:
    if not (current.get("mock") or baseline.get("mock")):
        return []
    return [
        "!! At least one side of this comparison is a MOCK run. The scripted persona",
        "!! behaves identically regardless of what changed, so the correctness axis is",
        "!! CONSTANT BY CONSTRUCTION here, and the cost axis is a token ESTIMATE",
        "!! (~4 chars/token), not API-measured. This proves the comparison MECHANISM",
        "!! works; it decides NOTHING about whether a real change helped or hurt.",
        "-" * 78,
    ]


def compare(current: dict, baseline: dict) -> str:
    """Render THIS run vs a STORED baseline — the run-over-run delta, per arm."""
    lines = [
        "=" * 78,
        "VS STORED BASELINE",
        "=" * 78,
        f"baseline: commit {baseline.get('git_commit', 'unknown')[:12]}  "
        f"captured {baseline.get('captured_at', '?')}"
        + ("  [MOCK]" if baseline.get("mock") else ""),
        f"current : commit {current.get('git_commit', 'unknown')[:12]}  "
        f"captured {current.get('captured_at', '?')}" + ("  [MOCK]" if current.get("mock") else ""),
        "-" * 78,
    ]
    lines.extend(_mock_banner(current, baseline))

    base_summaries = baseline.get("summaries", {})
    cur_summaries = current.get("summaries", {})
    lines.append(f"{'arm':<12}{'success Δpp':>14}{'$/run Δ':>12}{'$/success Δ':>14}{'verdict':>18}")
    lines.append("-" * 78)

    any_arm = False
    for arm in current.get("arms", []):
        cur = cur_summaries.get(arm)
        if not cur:
            continue
        old = base_summaries.get(arm)
        any_arm = True
        if not old:
            lines.append(f"{arm:<12}{'(no baseline data for this arm)':>58}")
            continue

        success_delta_pp = (cur["success"]["mean"] - old["success"]["mean"]) * 100
        cost_delta_pct = _pct_delta(cur["cost_per_run"]["mean"], old["cost_per_run"]["mean"])
        succ_cost_delta_pct = _pct_delta(cur["cost_per_success"], old["cost_per_success"])

        cheaper = cost_delta_pct < -0.5
        worse = success_delta_pp < -0.5
        better = success_delta_pp > 0.5
        if cheaper and worse:
            verdict = "BAD TRADE"
        elif worse:
            verdict = "REGRESSION"
        elif cheaper and not worse:
            verdict = "WIN"
        elif better:
            verdict = "improved"
        else:
            verdict = "steady"

        lines.append(
            f"{arm:<12}{success_delta_pp:>+13.1f}{cost_delta_pct:>+11.1f}%"
            f"{succ_cost_delta_pct:>+13.1f}%{verdict:>18}"
        )

    if not any_arm:
        lines.append("(no arms in common between this run and the baseline)")

    regressed = _per_task_regressions(current, baseline)
    if regressed:
        lines.append("")
        lines.append(
            f"PER-TASK REGRESSIONS (>{_TASK_REGRESSION_THRESHOLD_PP:.0f}pp success drop vs baseline)"
        )
        lines.append("-" * 78)
        lines.extend(regressed)

    lines.append("")
    lines.append("Δ is THIS RUN minus the STORED BASELINE — negative success Δ is a real")
    lines.append("regression regardless of what the cost axis does; a cheaper AND worse arm")
    lines.append("is a BAD TRADE, not a saving (the whole point of measuring both together).")
    return "\n".join(lines)


def _per_task_regressions(current: dict, baseline: dict) -> list[str]:
    cur_tasks = current.get("per_task_success", {})
    base_tasks = baseline.get("per_task_success", {})
    out: list[str] = []
    for task_id, cur_row in cur_tasks.items():
        old_row = base_tasks.get(task_id)
        if not old_row:
            continue
        for arm in current.get("arms", []):
            cv, ov = cur_row.get(arm), old_row.get(arm)
            if cv is None or ov is None:
                continue
            drop_pp = (cv - ov) * 100
            if drop_pp < -_TASK_REGRESSION_THRESHOLD_PP:
                out.append(f"  {task_id} [{arm}]: {ov * 100:.0f}% -> {cv * 100:.0f}%")
    return out
