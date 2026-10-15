"""MODEL routing evidence: which is the cheapest model that still gets each task RIGHT.

Deliberately NOT wired into ``__main__.py`` (that file plus ``model_client.py`` and
``baselines/`` are owned by another lane on this branch — see ``openrouter_client.py``'s
module doc). This is a standalone driver that reuses the harness's real, unmodified
building blocks — ``runner.run_one``, the 19-task set in ``tasks.py``, the outcome-based
scorer, arm ``A-real`` (the REAL production tool registry, not a shim) — and adds only
what those blocks do not yet have: a client factory that can hand ``run_one`` a model
from a DIFFERENT provider each time (Anthropic vs. an OpenAI-compatible endpoint via
OpenRouter), and correct cost accounting for the latter (see openrouter_client.py's
module doc for why the harness's own ``compute_cost_usd`` cannot price those models).

Every comparison runs on arm A-real: the real ``TOOL_SCHEMAS``, the real ``find_tools``,
the real ``execute_tool`` dispatch. The variable under test is the MODEL, not the tool
surface, so holding the surface constant at "what production actually offers" is the
right choice, not the A/B/C architecture comparison this package also supports.

Usage:

    # pilot: 3 tasks x N models x 1 rep — cheap, validates the adapters end to end
    .venv/bin/python3 -m backend.evals.tool_surface.model_routing_run \\
        --tasks simple_expense,statement_import_with_dup,near_miss_no_account --repeats 1

    # full run: all 19 tasks x N models x 3 reps
    .venv/bin/python3 -m backend.evals.tool_surface.model_routing_run --repeats 3

    # a subset of models
    .venv/bin/python3 -m backend.evals.tool_surface.model_routing_run --models sonnet,minimax

API keys are read ONCE, directly from the real ``~/.open-net-worth/data/model_config.json``
(never through ``ModelConfigStore``, which would resolve against the QA-isolated
``DATA_DIR`` bootstrap.py redirects to) and held only in this process's memory — never
written to a file, log, or commit. Data safety for everything else (the fixture book,
the ledger writes the tasks make) is unchanged from the rest of this package: bootstrap
redirects DATA_DIR/CACHE_DIR/LOGS_DIR before any backend import and the backend hard-
aborts if any of them resolves into the live tree.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

# Read provider keys directly off the real config file, BEFORE anything below imports
# bootstrap (which redirects DATA_DIR away from ~/.open-net-worth for the rest of this
# process). This is a plain file read, not routed through ModelConfigStore, precisely
# so it still sees the real keys after that redirect happens.
_REAL_MODEL_CONFIG = Path.home() / ".open-net-worth" / "data" / "model_config.json"


def _load_provider_keys() -> dict[str, str]:
    try:
        cfg = json.loads(_REAL_MODEL_CONFIG.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        print(f"WARNING: could not read {_REAL_MODEL_CONFIG}: {exc}", file=sys.stderr)
        return {}
    providers = cfg.get("providers", {})
    keys = {}
    for name, entry in providers.items():
        key = (entry or {}).get("api_key")
        if key:
            keys[name] = key
    return keys


_KEYS = _load_provider_keys()
if _KEYS.get("anthropic"):
    os.environ.setdefault("ANTHROPIC_API_KEY", _KEYS["anthropic"])

from backend.evals.tool_surface import bootstrap  # noqa: E402,F401  (must be first backend import)
from backend.evals.tool_surface import report as report_module  # noqa: E402
from backend.evals.tool_surface import runner as runner_module  # noqa: E402
from backend.evals.tool_surface import tasks as tasks_module  # noqa: E402
from backend.evals.tool_surface.model_client import AnthropicClient, ModelClient  # noqa: E402
from backend.evals.tool_surface.openrouter_client import OpenRouterClient  # noqa: E402
from backend.evals.tool_surface.spec import ArmSpec  # noqa: E402

OPENROUTER_BASE = "https://openrouter.ai/api/v1"

#: arm key -> (provider, model id). "provider" here means which client class to build,
#: not the openai_compatible/anthropic split model_config.json uses.
MODEL_REGISTRY: dict[str, tuple[str, str]] = {
    "sonnet": ("anthropic", "claude-sonnet-5"),
    "haiku": ("anthropic", "claude-haiku-4-5"),
    "minimax": ("openrouter", "minimax/minimax-m3"),
    "glm": ("openrouter", "z-ai/glm-5.2"),
}


def make_factory(model_keys: list[str]) -> tuple[object, dict[str, ModelClient]]:
    """One client per arm key, cached and reused across every task/rep for that model."""
    cache: dict[str, ModelClient] = {}

    def factory(spec: ArmSpec, task_id: str, ref: dict) -> ModelClient:
        key = spec.key
        if key not in cache:
            provider, model_id = MODEL_REGISTRY[key]
            if provider == "anthropic":
                cache[key] = AnthropicClient(model=model_id)
            elif provider == "openrouter":
                api_key = _KEYS.get("openrouter", "")
                cache[key] = OpenRouterClient(
                    model=model_id, api_key=api_key, base_url=OPENROUTER_BASE
                )
            else:  # pragma: no cover - defensive
                raise ValueError(f"unknown provider {provider!r} for arm {key!r}")
        return cache[key]

    return factory, cache


def _correct_openrouter_cost(record, client: ModelClient, seen: dict[str, float]) -> None:
    """Overwrite the harness's cost estimate with the REAL OpenRouter-reported delta.

    ``runner.compute_cost_usd`` always prices through ``cost_usd_anthropic`` regardless
    of model name (confirmed by reading it — it is not gated on a ``claude-`` prefix),
    so left alone it would silently price every minimax/glm run at Anthropic's default
    rate. ``OpenRouterClient`` accumulates the real ``usage.cost`` the endpoint reports
    across its lifetime; the delta since the last time we looked IS this run's real cost.
    """
    if not isinstance(client, OpenRouterClient):
        return
    key = client.name
    before = seen.get(key, 0.0)
    after = client.total_reported_cost_usd
    record.cost.usd = round(max(0.0, after - before), 6)
    record.cost.estimated = False
    seen[key] = after


def _category_breakdown(records: list) -> list[dict]:
    """Success rate per (category, model) — the axis the routing decision hinges on."""
    from collections import defaultdict

    grouped: dict[tuple[str, str], list] = defaultdict(list)
    categories: list[str] = []
    for r in records:
        if r.task_category not in categories:
            categories.append(r.task_category)
        grouped[(r.task_category, r.arm)].append(r)

    rows = []
    for cat in categories:
        row: dict = {"category": cat}
        for arm in dict.fromkeys(r.arm for r in records):
            rows_here = grouped.get((cat, arm), [])
            if not rows_here:
                continue
            n = len(rows_here)
            ok = sum(1 for r in rows_here if r.success)
            row[arm] = {
                "n": n,
                "success_rate": ok / n,
                "avg_cost": sum(r.cost.usd for r in rows_here) / n,
            }
        rows.append(row)
    return rows


def _failure_mode(record) -> str:
    """Classify a failed run's failure MODE — loud vs. silent, per the brief's concern.

    A silent wrong write (the intended change landed, but with a wrong amount/category/
    account) is the worst outcome this app can produce; a loud refusal or visible tool
    error is comparatively safe. This is a heuristic reading of the same signals the
    scorer already computes, not a new scoring axis.
    """
    if record.error:
        return "crash/exception (loud)"
    if record.unintended:
        return "unintended side effect (silent — extra/duplicate data landed)"
    if record.value_gates_success and record.value_ok is False:
        return "wrong value stated (silent — confidently wrong read)"
    if not record.intended:
        # A near-miss task that should refuse: intended=False + no write is CORRECT,
        # so a genuine failure here means either a bad write never happened when it
        # should have, or (for a near-miss) an unwanted write DID happen — the scorer
        # already folds that into `unintended`/`reasons`; if neither fired, the write
        # simply never landed (silent no-op) or a wrong-shaped write failed the check.
        reasons_text = " ".join(record.reasons).lower()
        if any(w in reasons_text for w in ("wrong", "expected", "instead of", "booked")):
            return "wrong data landed (silent — booked but incorrect)"
        return "no write landed when one was expected (silent no-op)"
    return "other"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--models", required=True)
    parser.add_argument("--tasks", default="")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--out", default="")
    parser.add_argument("--budget-ceiling", type=float, default=25.0)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)

    model_keys = [m.strip() for m in args.models.split(",") if m.strip()]
    unknown = [m for m in model_keys if m not in MODEL_REGISTRY]
    if unknown:
        parser.error(f"unknown model key(s) {unknown} — choose from {list(MODEL_REGISTRY)}")

    missing_keys = []
    for key in model_keys:
        provider, _ = MODEL_REGISTRY[key]
        if provider == "anthropic" and not _KEYS.get("anthropic"):
            missing_keys.append(key)
        if provider == "openrouter" and not _KEYS.get("openrouter"):
            missing_keys.append(key)
    if missing_keys:
        parser.error(f"no API key available for: {missing_keys}")

    selected = tasks_module.TASKS
    if args.tasks:
        wanted = {t.strip() for t in args.tasks.split(",") if t.strip()}
        selected = [t for t in tasks_module.TASKS if t.id in wanted]
        missing_tasks = wanted - {t.id for t in selected}
        if missing_tasks:
            parser.error(f"unknown task id(s): {sorted(missing_tasks)}")

    specs = [ArmSpec(key=key, surface="A-real", model=MODEL_REGISTRY[key][1]) for key in model_keys]

    factory, client_cache = make_factory(model_keys)
    reported_cost_seen: dict[str, float] = {}

    total = len(selected) * len(specs) * args.repeats
    print(
        f"Running {total} REAL API runs: {len(selected)} tasks x {len(specs)} models "
        f"x {args.repeats} reps  (surface=A-real, the real production tool registry)"
    )
    print(f"models: {[(s.key, s.model) for s in specs]}")
    print(f"budget ceiling: ${args.budget_ceiling:.2f}\n")

    records: list = []
    done = 0
    running_spend = 0.0
    t_start = time.time()

    for task in selected:
        for spec in specs:
            for rep in range(1, args.repeats + 1):
                done += 1
                record = runner_module.run_one(
                    task, spec, rep, factory, decoys=None, verbose=args.verbose
                )
                client = client_cache.get(spec.key)
                if client is not None:
                    _correct_openrouter_cost(record, client, reported_cost_seen)
                records.append(record)
                running_spend += record.cost.usd
                status = "PASS" if record.success else "FAIL"
                detail = "; ".join(record.reasons + record.unintended)[:90]
                print(
                    f"[{done:>3}/{total}] {task.id:<28} {spec.key:<8} rep{rep} "
                    f"-> {status:<4} ${record.cost.usd:.4f}  (spend so far: ${running_spend:.3f})"
                    f"{('  ' + detail) if detail else ''}"
                )
                if running_spend > args.budget_ceiling:
                    print(
                        f"\nBUDGET CEILING HIT (${running_spend:.2f} > ${args.budget_ceiling:.2f}) "
                        "— stopping early. Partial results below are still valid evidence "
                        "for the runs that completed.",
                        file=sys.stderr,
                    )
                    done = total  # exit both loops
                    break
            else:
                continue
            break
        else:
            continue
        break

    wall = time.time() - t_start
    arms = [s.key for s in specs]

    print(f"\nWall time: {wall:.1f}s   Actual real spend: ${running_spend:.4f}\n")
    for key in arms:
        client = client_cache.get(key)
        if isinstance(client, OpenRouterClient) and client.unreported_calls:
            print(
                f"NOTE: {key} had {client.unreported_calls}/{client.calls} calls with NO "
                "endpoint-reported cost — those runs' cost is likely undercounted as $0."
            )

    meta = {
        "surface": "A-real (real production tool registry — not a candidate, THE thing)",
        "models compared": ", ".join(f"{s.key}={s.model}" for s in specs),
        "tasks": len(selected),
        "repeats per cell": args.repeats,
        "total runs": len(records),
        "actual real spend (USD)": f"${running_spend:.4f}",
        "wall seconds": f"{wall:.1f}",
    }
    print(report_module.render(records, arms, meta))

    print("PER-CATEGORY SUCCESS RATE + AVG COST (the routing-decision axis)")
    print("-" * 78)
    breakdown = _category_breakdown(records)
    header = f"{'category':<20}" + "".join(f"{a:>14}" for a in arms)
    print(header)
    print("-" * 78)
    for row in breakdown:
        cells = ""
        for arm in arms:
            cell = row.get(arm)
            if cell:
                text = f"{cell['success_rate'] * 100:.0f}%/${cell['avg_cost']:.3f}"
            else:
                text = "-"
            cells += f"{text:>14}"
        print(f"{row['category']:<20}{cells}")
    print()

    print("FAILURE MODE BREAKDOWN (loud vs. silent — the thing that actually matters)")
    print("-" * 78)
    from collections import Counter

    for arm in arms:
        modes = Counter(_failure_mode(r) for r in records if r.arm == arm and not r.success)
        if not modes:
            print(f"[{arm}] no failures")
            continue
        print(f"[{arm}]")
        for mode, n in modes.most_common():
            print(f"  {n:>3}  {mode}")
    print()

    if args.out:
        Path(args.out).write_text(runner_module.records_to_json(records), encoding="utf-8")
        print(f"wrote raw records to {args.out}")

    passed = sum(1 for r in records if r.success)
    print(f"\n{passed}/{len(records)} runs passed.  Total real spend: ${running_spend:.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
