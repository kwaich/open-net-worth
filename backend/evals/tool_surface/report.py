"""Aggregation and reporting.

Every rate is reported as ``mean ± stddev`` across repetitions, never as a bare number.
With N=3 per task per arm the stddev is coarse, but a coarse spread is honest and a
single number is not: it hides whether an arm is reliably mediocre or wildly variable,
and those two failure modes call for different decisions.

The per-task breakdown is mandatory, not optional. An aggregate that conceals "arm C
fails every FX task" is worse than no comparison at all — so the report always prints
the per-task grid and explicitly calls out any task where arms disagree.
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Iterable

from backend.evals.tool_surface.runner import RunRecord


@dataclass
class Stat:
    mean: float
    stddev: float
    n: int

    def __str__(self) -> str:
        return f"{self.mean:.2f} ± {self.stddev:.2f}"

    def pct(self) -> str:
        return f"{self.mean * 100:5.1f}% ± {self.stddev * 100:4.1f}"


def stat(values: Iterable[float]) -> Stat:
    data = [float(v) for v in values]
    if not data:
        return Stat(0.0, 0.0, 0)
    spread = statistics.stdev(data) if len(data) > 1 else 0.0
    return Stat(statistics.mean(data), spread, len(data))


@dataclass
class ArmSummary:
    arm: str
    label: str
    success: Stat
    intended: Stat
    unintended: Stat
    wrong_value: Stat
    selection: Stat
    round_trips: Stat
    tool_calls: Stat
    discovery: Stat
    errors: Stat
    recovered: Stat
    input_tokens: Stat
    output_tokens: Stat
    wall: Stat
    runs: int
    estimated: bool
    api_calls: Stat
    uncached_in: Stat
    cache_write: Stat
    cache_read: Stat
    cost_per_run: Stat
    #: THE headline number: cost / success rate — what one CORRECT task really costs,
    #: because a failed task is paid for and then paid for again on the retry.
    cost_per_success: float
    failures: list[str] = field(default_factory=list)


def summarise_arm(arm: str, records: list[RunRecord]) -> ArmSummary:
    rows = [r for r in records if r.arm == arm]
    read_rows = [r for r in rows if r.value_ok is not None]
    success_rate = stat(1.0 if r.success else 0.0 for r in rows)
    mean_cost = stat(r.cost.usd for r in rows)
    cost_per_success = mean_cost.mean / success_rate.mean if success_rate.mean > 0 else float("inf")
    return ArmSummary(
        arm=arm,
        label=rows[0].arm_label if rows else "",
        api_calls=stat(r.cost.api_calls for r in rows),
        uncached_in=stat(r.cost.uncached_input_tokens for r in rows),
        cache_write=stat(r.cost.cache_write_tokens for r in rows),
        cache_read=stat(r.cost.cache_read_tokens for r in rows),
        cost_per_run=mean_cost,
        cost_per_success=cost_per_success,
        success=stat(1.0 if r.success else 0.0 for r in rows),
        intended=stat(1.0 if r.intended else 0.0 for r in rows),
        unintended=stat(1.0 if r.unintended else 0.0 for r in rows),
        wrong_value=stat(1.0 if r.value_ok is False else 0.0 for r in read_rows),
        selection=stat(1.0 if r.op_selection_correct else 0.0 for r in rows),
        round_trips=stat(r.round_trips for r in rows),
        tool_calls=stat(r.tool_calls for r in rows),
        discovery=stat(r.discovery_calls for r in rows),
        errors=stat(r.tool_errors for r in rows),
        recovered=stat(r.recovered_errors for r in rows),
        input_tokens=stat(r.input_tokens for r in rows),
        output_tokens=stat(r.output_tokens for r in rows),
        wall=stat(r.wall_seconds for r in rows),
        runs=len(rows),
        estimated=any(r.tokens_estimated for r in rows),
        failures=[
            f"{r.task_id}#{r.repetition}: " + "; ".join(r.reasons + r.unintended)
            for r in rows
            if not r.success
        ],
    )


def per_task_grid(records: list[RunRecord], arms: list[str]) -> list[dict]:
    """Success rate per (task, arm), so an aggregate can never hide a per-task cliff."""
    grouped: dict[tuple[str, str], list[RunRecord]] = defaultdict(list)
    for record in records:
        grouped[(record.task_id, record.arm)].append(record)

    task_ids: list[str] = []
    for record in records:
        if record.task_id not in task_ids:
            task_ids.append(record.task_id)

    grid = []
    for task_id in task_ids:
        row: dict = {"task": task_id}
        category = next((r.task_category for r in records if r.task_id == task_id), "")
        row["category"] = category
        for arm in arms:
            runs = grouped.get((task_id, arm), [])
            row[arm] = stat(1.0 if r.success else 0.0 for r in runs) if runs else None
        grid.append(row)
    return grid


def divergent_tasks(grid: list[dict], arms: list[str], threshold: float = 0.34) -> list[dict]:
    """Tasks where arms disagree by more than ``threshold`` — the interesting rows."""
    out = []
    for row in grid:
        values = [row[arm].mean for arm in arms if row.get(arm) is not None]
        if values and (max(values) - min(values)) > threshold:
            out.append(row)
    return out


def _render_cost_axis(summaries: list[ArmSummary]) -> str:
    """The two-axis table: cost against correctness, with the bad trade called out.

    A change that cuts cost 30% and correctness 10% must read as WORSE here, not as a
    saving — so the decision column is cost per SUCCESSFUL task, which folds the
    reliability penalty in automatically (a failed task is paid for twice: once to fail,
    once to retry).
    """
    mocked = any(s.estimated for s in summaries)
    lines = [
        "COST vs CORRECTNESS  (the decision table)",
        "-" * 78,
    ]
    if mocked:
        lines += [
            "!! MOCK RUN — THE CORRECTNESS AXIS IS NOT MEASURED HERE.",
            "!! The scripted persona behaves identically on every arm by construction, so",
            "!! equal correctness is an ARTIFACT, not a finding, and the cost deltas below",
            "!! are token ESTIMATES. This table proves the instrument works; it decides",
            "!! nothing. Verdicts are withheld until a real run supplies both axes.",
            "-" * 78,
        ]
    lines += [
        f"{'arm':<12}{'correct':>12}{'$/run':>12}{'$/SUCCESS':>14}{'vs baseline':>28}",
        "-" * 78,
    ]

    baseline = summaries[0] if summaries else None
    for summary in summaries:
        correct = f"{summary.success.mean * 100:.0f}% ± {summary.success.stddev * 100:.0f}"
        per_run = f"${summary.cost_per_run.mean:.4f}"
        per_success = (
            f"${summary.cost_per_success:.4f}"
            if summary.cost_per_success != float("inf")
            else "n/a"
        )

        verdict = ""
        if mocked:
            # Never render a WIN/BAD-TRADE verdict from a run whose correctness axis is
            # constant by construction — that is how a mock artifact becomes a claim.
            if baseline is not None and summary is not baseline:
                delta = _pct_delta(summary.cost_per_run.mean, baseline.cost_per_run.mean)
                verdict = f"{delta:+.0f}% cost (est.); correctness N/A"
            else:
                verdict = "(baseline)"
        elif baseline is not None and summary is not baseline:
            cost_delta = _pct_delta(summary.cost_per_run.mean, baseline.cost_per_run.mean)
            correct_delta = (summary.success.mean - baseline.success.mean) * 100
            cheaper = cost_delta < -0.5
            worse = correct_delta < -0.5
            if cheaper and worse:
                verdict = f"BAD TRADE {cost_delta:+.0f}% cost {correct_delta:+.0f}pp correct"
            elif cheaper and not worse:
                verdict = f"WIN {cost_delta:+.0f}% cost, correctness held"
            elif worse:
                verdict = f"WORSE {correct_delta:+.0f}pp correct"
            else:
                verdict = f"{cost_delta:+.0f}% cost {correct_delta:+.0f}pp correct"
        elif baseline is not None:
            verdict = "(baseline)"

        lines.append(
            f"{summary.arm:<12}{correct:>12}{per_run:>12}{per_success:>14}  {verdict:<28}".rstrip()
        )

    lines.append("")
    lines.append("$/SUCCESS = mean $/run divided by success rate — what one CORRECT task costs.")
    lines.append("A 'BAD TRADE' arm is cheaper per run but less correct: the saving is borrowed")
    lines.append("from reliability and is repaid, with interest, by every retry.")
    return "\n".join(lines)


def _pct_delta(value: float, baseline: float) -> float:
    if not baseline:
        return 0.0
    return (value - baseline) / baseline * 100.0


# ── Rendering ────────────────────────────────────────────────────────────────


def render(records: list[RunRecord], arms: list[str], meta: dict) -> str:
    lines: list[str] = []
    add = lines.append

    add("=" * 78)
    add("TOOL SURFACE A/B HARNESS — RESULTS")
    add("=" * 78)
    for key, value in meta.items():
        add(f"{key:>22}: {value}")
    add("")

    summaries = [summarise_arm(arm, records) for arm in arms]

    add("PER-ARM SUMMARY  (mean ± stddev across all runs)")
    add("-" * 78)
    header = f"{'metric':<22}" + "".join(f"{a:>18}" for a in arms)
    add(header)
    add("-" * 78)

    def row(label: str, attr: str, as_pct: bool = False) -> None:
        cells = ""
        for summary in summaries:
            value: Stat = getattr(summary, attr)
            cells += f"{(value.pct() if as_pct else str(value)):>18}"
        add(f"{label:<22}{cells}")

    row("success rate", "success", as_pct=True)
    row("  intended landed", "intended", as_pct=True)
    row("  had side effects", "unintended", as_pct=True)
    row("wrong-value rate", "wrong_value", as_pct=True)
    row("op selection ok", "selection", as_pct=True)
    add("")
    row("round trips", "round_trips")
    row("tool calls", "tool_calls")
    row("  discovery calls", "discovery")
    row("tool errors", "errors")
    row("  recovered", "recovered")
    add("")
    row("API calls", "api_calls")
    row("  uncached input tok", "uncached_in")
    row("  cache WRITE tok", "cache_write")
    row("  cache READ tok", "cache_read")
    row("output tokens", "output_tokens")
    row("wall seconds", "wall")
    add(f"{'runs per arm':<22}" + "".join(f"{s.runs:>18}" for s in summaries))
    add("")

    if any(s.estimated for s in summaries):
        add("NOTE: token counts are ESTIMATED (mock run, ~4 chars/token) — not API-measured.")
        add("")

    add(_render_cost_axis(summaries))

    add("PER-TASK SUCCESS RATE")
    add("-" * 78)
    add(f"{'task':<30}{'category':<16}" + "".join(f"{a:>10}" for a in arms))
    add("-" * 78)
    grid = per_task_grid(records, arms)
    for entry in grid:
        cells = ""
        for arm in arms:
            value = entry.get(arm)
            cells += f"{(f'{value.mean * 100:.0f}%' if value else '-'):>10}"
        add(f"{entry['task']:<30}{entry['category']:<16}{cells}")
    add("")

    diverging = divergent_tasks(grid, arms)
    if diverging:
        add("TASKS WHERE ARMS DISAGREE (>34pp spread) — read these before the aggregate")
        add("-" * 78)
        for entry in diverging:
            cells = ", ".join(
                f"{arm}={entry[arm].mean * 100:.0f}%" for arm in arms if entry.get(arm) is not None
            )
            add(f"  {entry['task']} ({entry['category']}): {cells}")
        add("")
    else:
        add("No task shows a >34pp spread between arms.")
        add("")

    add("FAILURES")
    add("-" * 78)
    any_failure = False
    for summary in summaries:
        if not summary.failures:
            continue
        any_failure = True
        add(f"[{summary.arm}]")
        for failure in summary.failures[:40]:
            add(f"  - {failure}")
        if len(summary.failures) > 40:
            add(f"  ... and {len(summary.failures) - 40} more")
    if not any_failure:
        add("  (none)")
    add("")
    return "\n".join(lines)


def cost_estimate(records: list[RunRecord], arms: list[str], tasks: int, repeats: int) -> str:
    """Actual spend for this run, plus the extrapolated cost of a FULL run.

    Costs come from ``backend.ai.models.pricing`` per run (see ``runner.compute_cost_usd``)
    rather than being recomputed here, so this never disagrees with the decision table.
    """
    lines = ["COST ESTIMATE", "-" * 78]
    total = 0.0
    for arm in arms:
        rows = [r for r in records if r.arm == arm]
        if not rows:
            continue
        spent = sum(r.cost.usd for r in rows)
        total += spent
        per_run = spent / len(rows)
        lines.append(
            f"  {arm:<12} {len(rows):>3} runs   ${per_run:.5f}/run   "
            f"${spent:.4f}   model={rows[0].model}"
        )
    lines.append(f"  {'TOTAL':<12} {len(records):>3} runs {'':>14}${total:.4f}")

    if records:
        per_run_overall = total / len(records)
        full = len(arms) * tasks * repeats
        lines.append("")
        lines.append(
            f"  FULL RUN ({len(arms)} arms x {tasks} tasks x {repeats} reps = {full} runs): "
            f"~${per_run_overall * full:.2f}"
        )
    if any(r.tokens_estimated for r in records):
        lines.append("  (MOCK RUN — token counts are ESTIMATED, so these dollars are indicative")
        lines.append("   of the harness's cost SHAPE, not a measured bill. Real run required.)")
    return "\n".join(lines)
