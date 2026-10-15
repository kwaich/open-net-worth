"""CLI: run the tool-surface A/B harness.

    # zero-cost proof that the harness and scorer work end to end
    python3 -m backend.evals.tool_surface --mock
    python3 -m backend.evals.tool_surface --mock --persona sloppy

    # real run (needs ANTHROPIC_API_KEY)
    python3 -m backend.evals.tool_surface --repeats 3
    python3 -m backend.evals.tool_surface --arms A,B,C,A-real --repeats 3

    # capture a baseline (run the REAL production surface, save it for later comparison).
    # Name the file after the commit you captured it at, verified with
    # `git merge-base --is-ancestor <sha> HEAD` — NOT "pre-merge": a baseline whose name
    # asserts an ancestry relationship nobody checked is worse than no baseline at all.
    python3 -m backend.evals.tool_surface --arm A-real --repeats 3 \\
        --save-baseline backend/evals/tool_surface/baselines/<verified-commit-sha>.json

    # compare a later run against a previously captured baseline
    python3 -m backend.evals.tool_surface --arm A-real --repeats 3 \\
        --baseline backend/evals/tool_surface/baselines/<verified-commit-sha>.json

Data safety: ``bootstrap`` sets ONW_QA_MODE=1 and redirects DATA_DIR/CACHE_DIR/LOGS_DIR
to a throwaway root before any backend import, and the backend hard-aborts if any of
them resolves into the live ~/.open-net-worth tree. No server is started, so no port is
used at all.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from backend.evals.tool_surface import baseline as baseline_module
from backend.evals.tool_surface import bootstrap  # noqa: F401  (must be first)
from backend.evals.tool_surface import report as report_module
from backend.evals.tool_surface import runner as runner_module
from backend.evals.tool_surface import tasks as tasks_module
from backend.evals.tool_surface.arms import DEFAULT_ARMS
from backend.evals.tool_surface.spec import parse_arm


def build_decoys(count: int) -> list[str]:
    """Real production tool names this harness does not implement.

    Padding every arm's catalog with these reproduces the real difficulty of picking
    the right tool out of ~271 names. Identical across arms, so it cannot bias the
    comparison; calling one is a genuine selection error.
    """
    if count <= 0:
        return []
    try:
        from backend.ai.tools import TOOL_SCHEMAS

        names = sorted(s["name"] for s in TOOL_SCHEMAS)
    except Exception:
        return []

    from backend.evals.tool_surface import ops as ops_module

    ours = {o.tool_name for o in ops_module.all_ops().values()} | {
        o.name for o in ops_module.all_ops().values()
    }
    return [n for n in names if n not in ours][:count]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tool_surface", description=__doc__)
    parser.add_argument(
        "--arm",
        action="append",
        default=[],
        metavar="SPEC",
        help=(
            "an arm to compare; repeatable. 'A' | 'haiku=A,model=claude-haiku-4-5' | "
            "'terse=A,descriptions=compressed' | 'small=C,result_budget=800'. "
            f"Default: {' '.join(DEFAULT_ARMS)}"
        ),
    )
    parser.add_argument("--repeats", type=int, default=3, help="runs per task per arm (min 3)")
    parser.add_argument("--tasks", default="", help="comma-separated task ids (default: all)")
    parser.add_argument("--model", default="", help="Anthropic model id (required for real runs)")
    parser.add_argument("--mock", action="store_true", help="scripted responses, zero API cost")
    parser.add_argument(
        "--persona", default="perfect", choices=["perfect", "sloppy"], help="mock persona"
    )
    parser.add_argument(
        "--decoys", type=int, default=300, help="catalog decoy tool names (0 = off)"
    )
    parser.add_argument("--out", default="", help="write the JSON run records here")
    parser.add_argument(
        "--save-baseline",
        default="",
        metavar="PATH",
        help="capture this run's per-arm summary + per-task grid to PATH, stamped with "
        "the current git commit, for a LATER run to compare against via --baseline",
    )
    parser.add_argument(
        "--baseline",
        default="",
        metavar="PATH",
        help="load a baseline previously written by --save-baseline and print THIS "
        "run's delta against it (cost and correctness together, per arm)",
    )
    parser.add_argument("--verbose", action="store_true", help="print each tool call")
    args = parser.parse_args(argv)
    if not args.mock and not args.model:
        parser.error("--model is required for a real run")

    try:
        specs = [parse_arm(a, default_model=args.model) for a in (args.arm or list(DEFAULT_ARMS))]
    except ValueError as exc:
        parser.error(str(exc))
    seen: set[str] = set()
    for spec in specs:
        if spec.key in seen:
            parser.error(f"duplicate arm key {spec.key!r} — give each arm a distinct name")
        seen.add(spec.key)
    arms = [s.key for s in specs]

    selected = tasks_module.TASKS
    if args.tasks:
        wanted = {t.strip() for t in args.tasks.split(",") if t.strip()}
        selected = [t for t in tasks_module.TASKS if t.id in wanted]
        missing = wanted - {t.id for t in selected}
        if missing:
            parser.error(f"unknown task id(s): {sorted(missing)}")

    if args.mock:
        client_factory = runner_module.make_mock_factory(args.persona)
        model_label = f"mock:{args.persona}"
    else:
        try:
            client_factory = runner_module.make_anthropic_factory()
        except Exception as exc:
            print(f"ERROR: cannot start a real run — {exc}", file=sys.stderr)
            print("Use --mock for a zero-cost run.", file=sys.stderr)
            return 2
        model_label = args.model

    decoys = build_decoys(args.decoys)
    records: list[runner_module.RunRecord] = []
    total = len(selected) * len(specs) * args.repeats
    done = 0

    print(f"Running {total} runs: {len(selected)} tasks x {len(arms)} arms x {args.repeats} reps")
    print(f"model={model_label}  decoys={len(decoys)}  data_dir={bootstrap.EVAL_ROOT}\n")

    for task in selected:
        for spec in specs:
            for rep in range(1, args.repeats + 1):
                done += 1
                print(f"[{done:>3}/{total}] {task.id:<30} arm {spec.key:<10} rep {rep}", flush=True)
                record = runner_module.run_one(
                    task, spec, rep, client_factory, decoys=decoys, verbose=args.verbose
                )
                records.append(record)
                status = "PASS" if record.success else "FAIL"
                detail = "; ".join(record.reasons + record.unintended)[:110]
                print(f"        -> {status}{('  ' + detail) if detail else ''}")

    print()
    meta = {
        "model": model_label,
        "arms": " | ".join(f"{s.key} ({s.label()})" for s in specs),
        "tasks": len(selected),
        "repeats per cell": args.repeats,
        "total runs": len(records),
        "catalog decoys": len(decoys),
        "fixture": "deterministic seed book (see fixture.py)",
        "data dir": str(bootstrap.EVAL_ROOT),
    }
    try:
        from backend.evals.tool_surface.introspect import real_introspect_status

        meta["arm C describe"] = real_introspect_status()
    except Exception:
        pass

    # Informational only — NOT wired into any arm's live turn loop, and NOT included in
    # this run's dollar figures above. The real system prompt stamps the wall-clock date
    # inline (`ai.prompts.build_system_prompt`), which would fight the fixture's frozen
    # TODAY if used as a task's actual system prompt, so this reports SIZE ONLY (the
    # lever the system-prompt-cut lane is working). The chars//4 figure below is a rough
    # estimate that varies with THIS run's live data state (portfolio/profile/memory
    # content), not a fixed constant — see baselines/README.md for an API-MEASURED
    # count (Anthropic `messages.count_tokens`) taken against a specific, reproducible
    # data state, and why that number legitimately differs from this one and from
    # SYSTEM.md's cited figure without any of the three being wrong.
    try:
        from backend.ai.prompts import build_system_prompt

        real_prompt = build_system_prompt()
        meta["real system prompt SIZE, informational (chars//4 estimate, NOT in $ above)"] = (
            f"{len(real_prompt):,} chars (~{len(real_prompt) // 4:,} tok est.) — "
            "size varies with live data state; see baselines/README.md for a measured count"
        )
    except Exception:
        pass

    print(report_module.render(records, arms, meta))
    print(report_module.cost_estimate(records, arms, len(selected), args.repeats))

    if args.out:
        Path(args.out).write_text(runner_module.records_to_json(records), encoding="utf-8")
        print(f"\nwrote {args.out}")

    if args.baseline or args.save_baseline:
        current_doc = baseline_module.capture(records, arms, meta, mock=args.mock)

        if args.baseline:
            baseline_path = Path(args.baseline)
            if baseline_path.exists():
                stored = baseline_module.load(baseline_path)
                print(baseline_module.compare(current_doc, stored))
            else:
                print(f"\nNO BASELINE at {baseline_path} — nothing to compare against.")

        if args.save_baseline:
            baseline_module.save(current_doc, Path(args.save_baseline))
            print(
                f"\nwrote baseline {args.save_baseline}  (commit {current_doc['git_commit'][:12]})"
            )

    failures = sum(1 for r in records if not r.success)
    print(f"\n{len(records) - failures}/{len(records)} runs passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
