"""Accuracy-evaluation harness for the Transaction Match & Dedup engine.

Usage:
    python -m backend.scripts.dedup_eval [--baseline <report.json>] [--out <file>]
                                         [--llm] [--include-hardening]

Loads every fixture from tests/dedup/fixtures/*.json, runs EVERY incoming row
through the engine per case (not just the first), and produces:
  - A per-scenario pass/fail table on stdout.
  - A per-class confusion matrix over predicted × expected outcomes
    {merge/noop, pair, review, create} with precision/recall/F1.
  - dedup-eval-report.json with the full scorecard.
  - Non-zero exit if the deterministic hard gate fails (false-merge-rate > 0 on
    must-NOT-merge cases, or any must-NOT-drop / idempotency case is wrong).

``--baseline <report.json>``: diff a new run against a saved report and print
the delta so any tuning change yields one comparable number.

``--out <file>``: write a human-readable markdown table (in addition to JSON).
Defaults to stdout only.

``--llm``: replay fence-band cases through matching.judge_pair + matching.combine
(async, integration-marked). Skipped gracefully without an API key.  Not the
default; never called in tests except behind the integration marker.

``--include-hardening``: include phase=hardening fixtures in the run and gate.
By default, hardening fixtures are loaded and tracked (for accuracy snapshot)
but are EXCLUDED from the pass/fail gate and confusion-matrix summary.

Fixture schema (extends existing):
  phase: str  (optional) — "hardening" marks fixtures whose expected behavior
    the current engine does not yet produce. They are always run (for the
    baseline snapshot) but skipped in the gate unless --include-hardening.
  max_score: float  (optional) — assert the engine's best candidate score ≤
    this value (used to verify "not auto-merge-eligible" cases).
  auto_eligible: bool  (optional, false) — if false, assert score < SCORE_STRONG.
"""

from __future__ import annotations

# ── DATA SAFETY: redirect all backend paths to throwaway dirs BEFORE any
# backend module is imported (mirrors tests/conftest.py).
import os
import tempfile as _tempfile

if "DATA_DIR" not in os.environ or "/.open-net-worth/" in os.environ.get("DATA_DIR", ""):
    _tmp = _tempfile.mkdtemp(prefix="onw-dedup-eval-")
    os.environ["DATA_DIR"] = _tmp + "/data"
    os.environ["CACHE_DIR"] = _tmp + "/cache"

import asyncio
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ── constants / helpers ────────────────────────────────────────────────────────

FIXTURES_DIR = Path(__file__).parents[2] / "tests" / "dedup" / "fixtures"
REPORT_PATH = Path(__file__).parents[2] / "dedup-eval-report.json"

# Outcome label sets for confusion-matrix bookkeeping.
_MUST_NOT_MERGE_VALUES = {"keep_both", "noop"}
_MUST_MERGE_VALUES = {"merge", "pair"}

# Canonical action labels used in the confusion matrix.
# "predicted" comes from engine_action; "expected" comes from expect.engine_action.
_ACTIONS = ("noop", "create", "review", "pair")  # "pair" engine maps to review+become_transfer


# ── Case loading ───────────────────────────────────────────────────────────────


def load_fixtures(*, include_hardening: bool = False) -> list[dict]:
    """Return every case object from every fixture JSON file in FIXTURES_DIR.

    When ``include_hardening`` is False (default), hardening fixtures are still
    returned but each carries ``_gate_excluded=True`` so callers can exclude them
    from the hard gate while still recording them in the report.
    """
    cases: list[dict] = []
    for path in sorted(FIXTURES_DIR.glob("*.json")):
        data = json.loads(path.read_text())
        raw: list[dict] = data if isinstance(data, list) else [data]
        for case in raw:
            is_hardening = case.get("phase") == "hardening"
            case["_gate_excluded"] = is_hardening and not include_hardening
            cases.append(case)
    return cases


# ── Isolated store setup ───────────────────────────────────────────────────────


def _make_isolated_stores(tmp_dir: Path) -> tuple[Any, Any]:
    """Create fresh AccountStore + TransactionStore isolated to their OWN per-case
    SQLite database under tmp_dir.

    PLAN 3 TASK 4: these stores are DB-backed now (Core, tables `accounts`/
    `transactions`) — there is no more file path to redirect per case. Isolation
    is now a fresh, migrated-to-head SQLite file per case instead of a fresh JSON
    file pair; `AccountStore(engine=...)`/`TransactionStore(engine=...)` bind to
    it directly (never the process-wide `get_engine()` singleton), so fixture
    cases that reuse the same account/transaction id (several genuinely do — every
    case is its own self-contained scenario in the JSON fixtures) no longer
    collide against a real DB primary key the way a shared engine would."""
    from alembic import command

    from backend.db.account_store import AccountStore
    from backend.db.alembic_config import make_config
    from backend.db.engine import _make_engine
    from backend.db.transaction_store import TransactionStore

    url = f"sqlite:///{tmp_dir / 'eval.db'}"
    command.upgrade(make_config(url), "head")
    engine = _make_engine(url)

    accounts = AccountStore(engine=engine)
    txns = TransactionStore(engine=engine)
    return accounts, txns


# ── Per-case evaluation ────────────────────────────────────────────────────────


@dataclass
class RowResult:
    """Result for one incoming row within a case."""

    row_index: int
    engine_action: str
    engine_class: str | None
    become_transfer: bool
    canonical_id: str | None
    best_score: float | None


@dataclass
class CaseResult:
    case_id: str
    label: str
    phase: str  # "" for standard, "hardening" for post-hardening targets
    gate_excluded: bool  # True when hardening and --include-hardening not passed
    must: str
    decided_by: str
    pending_l3: bool
    # Aggregate over all incoming rows (worst-case / first-row policy documented below).
    engine_action: str
    engine_class: str | None
    become_transfer: bool
    canonical_id: str | None
    best_score: float | None  # score of the best candidate for the row that drove engine_action
    expected_action: str
    expected_class: str | None
    expected_canonical: str | None
    expected_become_transfer: bool
    # Optional fixture-level score assertions.
    max_score: float | None
    auto_eligible_assert: bool | None  # None = not specified; False = must be < SCORE_STRONG
    passed: bool
    false_merge: bool
    miss: bool
    row_results: list[RowResult] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _evaluate_case(case: dict, tmp_dir: Path, *, llm_mode: bool = False) -> CaseResult:
    """Run one fixture case through the deterministic engine and record the result.

    ALL incoming rows are now iterated. The "driving" result used for the scorecard
    is the one with the highest-severity action following the precedence:
      review > noop > create
    (i.e. if ANY row returns review/pair, that drives the case verdict — the most
    conservative reading of the batch outcome.)
    """
    from backend.services import ledger as ledger_service
    from backend.services import matching

    accounts, txns = _make_isolated_stores(tmp_dir)
    ledger_service.accounts = accounts
    ledger_service.transactions = txns

    # Seed accounts.
    for acc in case.get("accounts", []):
        accounts.create(acc)

    # Seed existing ledger rows (direct append, bypassing write choke).
    id_map: dict[str, str] = {}
    for row in case.get("existing_ledger", []):
        stored = txns.append(dict(row))
        if row.get("id"):
            id_map[row["id"]] = stored.get("id", row["id"])

    expect = case.get("expect", {})
    expected_action = expect.get("engine_action", "")
    expected_class = expect.get("action_class")
    expected_canonical_key = expect.get("canonical")
    expected_canonical = id_map.get(expected_canonical_key, expected_canonical_key)
    expected_bt = bool(expect.get("become_transfer", False))
    max_score = expect.get("max_score")
    auto_eligible_assert = expect.get("auto_eligible")  # None or bool

    pending_l3 = bool(expect.get("pending_l3", False))
    decided_by = case.get("decided_by", "L0-L2")
    must = case.get("must", "")
    phase = case.get("phase", "")
    gate_excluded = bool(case.get("_gate_excluded", False))

    incoming_rows = case.get("incoming", [])
    # Optional per-case FX rates (needed for cross-currency fixtures, F15).
    case_rates: dict | None = case.get("rates") or None

    # Build the candidate pool from stored existing rows so the engine can match
    # across accounts (mirrors what the import-preview surface does).
    stored_candidates = list(id_map.values())
    base_candidate_pool: list[dict] = [txns.find_by_id(sid) for sid in stored_candidates if sid]
    base_candidate_pool = [c for c in base_candidate_pool if c]

    # Prior-incoming accumulator: rows processed earlier in THIS batch are added here so
    # later rows can match against them (H01: intra-batch transfer pair surfacing).
    prior_incoming: list[dict] = []

    # Action severity order: review is highest (most conservative = most flagging),
    # then noop, then create. We keep the worst (highest-severity) outcome.
    _severity = {"review": 3, "pair": 3, "merge": 3, "noop": 2, "create": 1}

    row_results: list[RowResult] = []
    driving_action = "create"
    driving_class: str | None = None
    driving_bt = False
    driving_canonical: str | None = None
    driving_score: float | None = None

    for idx, row in enumerate(incoming_rows):
        # Merge existing-ledger candidates with prior incoming rows so intra-batch pairs work.
        combined_pool = base_candidate_pool + prior_incoming
        res = matching.check(
            dict(row),
            profile=matching.HEADLESS,
            candidates=combined_pool if combined_pool else None,
            rates=case_rates,
        )
        prior_incoming.append(dict(row))
        row_action = res.action
        row_class = res.best.klass.value if res.best else None
        row_bt = res.become_transfer
        row_canonical = res.canonical_id
        row_score = res.best.score if res.best else None

        # LLM mode: for fence-band (review) results, replay through judge_pair + combine.
        if llm_mode and res.action == "review" and res.best is not None:
            row_action, row_class, row_bt, row_canonical, row_score = _llm_replay(
                dict(row), res, row_action, row_class, row_bt, row_canonical, row_score
            )

        rr = RowResult(
            row_index=idx,
            engine_action=row_action,
            engine_class=row_class,
            become_transfer=row_bt,
            canonical_id=row_canonical,
            best_score=row_score,
        )
        row_results.append(rr)

        if _severity.get(row_action, 0) >= _severity.get(driving_action, 0):
            driving_action = row_action
            driving_class = row_class
            driving_bt = row_bt
            driving_canonical = row_canonical
            driving_score = row_score

    engine_action = driving_action
    engine_class = driving_class
    become_transfer = driving_bt
    canonical_id = driving_canonical
    best_score = driving_score

    # Normalise expected_class: "none" → None.
    norm_expected_class: str | None = (
        None if expected_class in (None, "", "none") else expected_class
    )

    # Score assertions.
    score_ok = True
    if max_score is not None and best_score is not None:
        score_ok = best_score <= max_score
    if auto_eligible_assert is False and best_score is not None:
        from backend.services.matching import SCORE_STRONG

        score_ok = score_ok and (best_score < SCORE_STRONG)

    # Determine pass.
    if pending_l3 or gate_excluded:
        passed = True  # pending-L3 and hardening (gate_excluded) never fail the gate
    else:
        action_ok = engine_action == expected_action
        class_ok = norm_expected_class is None or engine_class == norm_expected_class
        bt_ok = (not expected_bt) or (become_transfer == expected_bt)
        canonical_ok = expected_canonical in (None, "") or canonical_id == expected_canonical
        passed = action_ok and class_ok and bt_ok and canonical_ok and score_ok

    # Gate metrics: only on non-gate_excluded, non-pending-l3 cases.
    gated = not gate_excluded and not pending_l3
    false_merge = gated and must in _MUST_NOT_MERGE_VALUES and engine_action == "review"
    miss = gated and must in _MUST_MERGE_VALUES and engine_action == "create"

    notes: list[str] = []
    note = expect.get("engine_action_note")
    if note:
        notes.append(note)
    if max_score is not None and not score_ok:
        notes.append(f"score assertion failed: best_score={best_score} > max_score={max_score}")

    return CaseResult(
        case_id=case["id"],
        label=case.get("label", case["id"]),
        phase=phase,
        gate_excluded=gate_excluded,
        must=must,
        decided_by=decided_by,
        pending_l3=pending_l3,
        engine_action=engine_action,
        engine_class=engine_class,
        become_transfer=become_transfer,
        canonical_id=canonical_id,
        best_score=best_score,
        expected_action=expected_action,
        expected_class=expected_class,
        expected_canonical=expected_canonical,
        expected_become_transfer=expected_bt,
        max_score=max_score,
        auto_eligible_assert=auto_eligible_assert,
        passed=passed,
        false_merge=false_merge,
        miss=miss,
        row_results=row_results,
        notes=notes,
    )


def _llm_replay(
    row: dict,
    res: Any,
    row_action: str,
    row_class: str | None,
    row_bt: bool,
    row_canonical: str | None,
    row_score: float | None,
) -> tuple[str, str | None, bool, str | None, float | None]:
    """Replay a fence-band result through judge_pair + combine (LLM mode, async)."""
    from backend.services import matching

    try:
        from backend.db.dedup_config_store import DedupConfigStore

        policy = DedupConfigStore().read().get("dedup_policy", "auto_transfers")
    except Exception:
        policy = "auto_transfers"

    candidate_txn = res.best.txn if res.best else None
    if candidate_txn is None:
        return row_action, row_class, row_bt, row_canonical, row_score

    async def _judge() -> tuple[str | None, str]:
        return await matching.judge_pair(row, candidate_txn)

    try:
        verdict, _reason = asyncio.run(_judge())
    except Exception:
        return row_action, row_class, row_bt, row_canonical, row_score

    combined = matching.combine(res, policy, llm_verdict=verdict)
    if combined == "auto_merge":
        row_action = "review"  # auto_merge is still surfaced as review in the scorecard
    return row_action, row_class, row_bt, row_canonical, row_score


# ── Confusion matrix ───────────────────────────────────────────────────────────


# Map engine_action → canonical class for the confusion matrix.
# "review" covers both near_duplicate and transfer_pair (the action is the same).
def _action_to_class(action: str, klass: str | None) -> str:
    if action == "noop":
        return "noop"
    if action == "create":
        return "create"
    if action == "review" and klass == "transfer_pair":
        return "pair"
    if action == "review":
        return "review"
    return action


def _must_to_class(must: str, expected_class: str | None) -> str:
    if must == "noop":
        return "noop"
    if must in ("keep_both",):
        return "create"
    if must == "pair":
        return "pair"
    if must == "merge":
        return "review"
    return "create"


def _build_confusion_matrix(results: list[CaseResult]) -> dict:
    """Build a confusion matrix over {noop, create, review, pair}."""
    # Only use deterministic, gated (non-hardening) cases.
    gated = [r for r in results if not r.pending_l3 and not r.gate_excluded]
    labels = ["noop", "create", "review", "pair"]
    # matrix[expected][predicted] = count
    matrix: dict[str, dict[str, int]] = {lbl: {l2: 0 for l2 in labels} for lbl in labels}

    for r in gated:
        exp = _must_to_class(r.must, r.expected_class)
        pred = _action_to_class(r.engine_action, r.engine_class)
        if exp in matrix and pred in matrix[exp]:
            matrix[exp][pred] += 1

    # Per-class P/R/F1.
    per_class: dict[str, dict[str, float | int]] = {}
    for lbl in labels:
        tp = matrix[lbl][lbl]
        fp = sum(matrix[other][lbl] for other in labels if other != lbl)
        fn = sum(matrix[lbl][other] for other in labels if other != lbl)
        prec = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        rec = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        f1 = 2 * prec * rec / (prec + rec) if (prec + rec) > 0 else 0.0
        per_class[lbl] = {
            "tp": tp,
            "fp": fp,
            "fn": fn,
            "precision": round(prec, 4),
            "recall": round(rec, 4),
            "f1": round(f1, 4),
        }

    return {"matrix": matrix, "per_class": per_class, "labels": labels}


# ── Scorecard ──────────────────────────────────────────────────────────────────


def _build_scorecard(results: list[CaseResult]) -> dict:
    """Build the full scorecard dict from a list of evaluated results."""
    total = len(results)
    hardening = [r for r in results if r.phase == "hardening"]
    l3_pending = [r for r in results if r.pending_l3 and not r.gate_excluded]
    # "deterministic" = standard (non-hardening) cases.
    standard = [r for r in results if not r.gate_excluded]
    deterministic = [r for r in standard if not r.pending_l3]

    passed_det = sum(1 for r in deterministic if r.passed)
    false_merges = [r for r in deterministic if r.false_merge]
    misses = [r for r in deterministic if r.miss]
    false_merge_rate = len(false_merges) / max(len(deterministic), 1)
    miss_rate = len(misses) / max(len(deterministic), 1)

    must_not_total = sum(1 for r in deterministic if r.must in _MUST_NOT_MERGE_VALUES)
    must_not_correct = sum(
        1 for r in deterministic if r.must in _MUST_NOT_MERGE_VALUES and not r.false_merge
    )
    must_merge_total = sum(1 for r in deterministic if r.must in _MUST_MERGE_VALUES)
    must_merge_correct = sum(
        1 for r in deterministic if r.must in _MUST_MERGE_VALUES and not r.miss
    )

    confusion = _build_confusion_matrix(results)

    # Hardening summary: which ones pass/fail on the current engine.
    hardening_pass = sum(1 for r in hardening if r.engine_action == r.expected_action)
    hardening_fail = [r.case_id for r in hardening if r.engine_action != r.expected_action]

    return {
        "total_cases": total,
        "standard_cases": len(standard),
        "hardening_cases": len(hardening),
        "deterministic_cases": len(deterministic),
        "l3_pending_cases": len(l3_pending),
        "deterministic_passed": passed_det,
        "deterministic_failed": len(deterministic) - passed_det,
        "false_merge_count": len(false_merges),
        "false_merge_rate": round(false_merge_rate, 4),
        "miss_count": len(misses),
        "miss_rate": round(miss_rate, 4),
        "must_not_merge": {
            "total": must_not_total,
            "correct": must_not_correct,
        },
        "must_merge": {
            "total": must_merge_total,
            "correct": must_merge_correct,
        },
        "confusion_matrix": confusion,
        "hardening_summary": {
            "cases": len(hardening),
            "pass_on_current_engine": hardening_pass,
            "fail_on_current_engine": len(hardening) - hardening_pass,
            "failing_ids": hardening_fail,
        },
        "per_case": [
            {
                "id": r.case_id,
                "label": r.label,
                "phase": r.phase or "standard",
                "gate_excluded": r.gate_excluded,
                "must": r.must,
                "decided_by": r.decided_by,
                "pending_l3": r.pending_l3,
                "engine_action": r.engine_action,
                "engine_class": r.engine_class,
                "best_score": r.best_score,
                "expected_action": r.expected_action,
                "expected_class": r.expected_class,
                "passed": r.passed,
                "false_merge": r.false_merge,
                "miss": r.miss,
                "notes": r.notes,
            }
            for r in results
        ],
    }


# ── Printing ───────────────────────────────────────────────────────────────────


def _print_table(results: list[CaseResult], scorecard: dict) -> None:
    col = 56
    print()
    print("=" * (col + 32))
    print("  DEDUP EVAL — deterministic engine (HEADLESS / L0–L2)")
    print("=" * (col + 32))
    print(f"  {'SCENARIO':<{col}} {'MUST':<12} {'ENGINE':<18} {'PASS'}")
    print("-" * (col + 32))
    for r in results:
        if r.gate_excluded:
            status = "SKIP[hardening]"
        elif r.pending_l3:
            status = "OK [L3-pending]"
        else:
            status = "OK" if r.passed else "FAIL"
        label = r.label[:col]
        klass = f"/{r.engine_class}" if r.engine_class else ""
        engine_str = f"{r.engine_action}{klass}"
        print(f"  {label:<{col}} {r.must:<12} {engine_str:<18} {status}")
    print("-" * (col + 32))

    det = scorecard
    det_cases = det["deterministic_cases"]
    print(f"\n  Standard deterministic : {det_cases}  (L3-pending: {det['l3_pending_cases']})")
    print(f"  Passed                 : {det['deterministic_passed']} / {det_cases}")
    fm = det["false_merge_rate"]
    mr = det["miss_rate"]
    print(f"  False-merge rate       : {fm:.1%}  (target: 0%)")
    print(f"  Miss rate              : {mr:.1%}")

    hs = det["hardening_summary"]
    if hs["cases"]:
        print(
            f"\n  Hardening fixtures     : {hs['cases']} total "
            f"({hs['pass_on_current_engine']} pass on current engine, "
            f"{hs['fail_on_current_engine']} fail)"
        )
        if hs["failing_ids"]:
            print("  Hardening failures (expected — pre-hardening):")
            for fid in hs["failing_ids"]:
                print(f"      {fid}")

    if det["false_merge_count"]:
        print(
            f"\n  *** {det['false_merge_count']} FALSE MERGE(S) — "
            "must-NOT cases flagged for review ***"
        )
        for r in results:
            if r.false_merge:
                print(f"      {r.case_id}")

    # Per-class P/R/F1 summary.
    cm = scorecard.get("confusion_matrix", {})
    per_class = cm.get("per_class", {})
    if per_class:
        print("\n  Per-class P/R/F1 (standard deterministic cases only):")
        print(f"    {'CLASS':<12}  {'P':>6}  {'R':>6}  {'F1':>6}  {'TP':>4}  {'FP':>4}  {'FN':>4}")
        for lbl, stats in per_class.items():
            print(
                f"    {lbl:<12}  {stats['precision']:>6.3f}  {stats['recall']:>6.3f}  "
                f"{stats['f1']:>6.3f}  {stats['tp']:>4}  {stats['fp']:>4}  {stats['fn']:>4}"
            )
    print()


def _print_delta(new: dict, baseline: dict) -> None:
    print("\n  === BASELINE COMPARISON ===")
    for key in ("false_merge_rate", "miss_rate", "deterministic_passed"):
        old_val = baseline.get(key, 0)
        new_val = new.get(key, 0)
        delta = new_val - old_val
        sign = "+" if delta >= 0 else ""
        print(f"  {key:<28}: {old_val} -> {new_val}  ({sign}{delta:.4f})")
    print()


def _write_markdown(results: list[CaseResult], scorecard: dict, out_path: Path) -> None:
    """Write a human-readable markdown accuracy report to out_path."""
    lines: list[str] = []
    lines.append("# Dedup Eval — Accuracy Report\n")
    lines.append(f"**Total cases**: {scorecard['total_cases']}  ")
    lines.append(f"**Standard deterministic**: {scorecard['deterministic_cases']}  ")
    lines.append(f"**L3-pending**: {scorecard['l3_pending_cases']}  ")
    lines.append(f"**Hardening (gate-excluded)**: {scorecard['hardening_cases']}  \n")

    lines.append(f"**False-merge rate**: {scorecard['false_merge_rate']:.1%} (target: 0%)  ")
    lines.append(f"**Miss rate**: {scorecard['miss_rate']:.1%}  \n")

    # Confusion matrix.
    cm = scorecard.get("confusion_matrix", {})
    per_class = cm.get("per_class", {})
    if per_class:
        lines.append("## Per-class Precision / Recall / F1\n")
        lines.append("| Class | Precision | Recall | F1 | TP | FP | FN |")
        lines.append("|---|---|---|---|---|---|---|")
        for lbl, stats in per_class.items():
            lines.append(
                f"| {lbl} | {stats['precision']:.3f} | {stats['recall']:.3f} | "
                f"{stats['f1']:.3f} | {stats['tp']} | {stats['fp']} | {stats['fn']} |"
            )
        lines.append("")

    # Confusion matrix table.
    matrix = cm.get("matrix", {})
    labels = cm.get("labels", [])
    if matrix and labels:
        lines.append("## Confusion Matrix (expected → predicted)\n")
        header = "| Expected \\ Predicted | " + " | ".join(labels) + " |"
        sep = "|---|" + "---|" * len(labels)
        lines.append(header)
        lines.append(sep)
        for exp in labels:
            row_vals = " | ".join(str(matrix.get(exp, {}).get(pred, 0)) for pred in labels)
            lines.append(f"| **{exp}** | {row_vals} |")
        lines.append("")

    # Per-scenario table.
    lines.append("## Per-scenario Results\n")
    lines.append("| ID | Label | Phase | Must | Engine Action | Pass |")
    lines.append("|---|---|---|---|---|---|")
    for r in results:
        phase_tag = r.phase or "standard"
        klass = f"/{r.engine_class}" if r.engine_class else ""
        engine_str = f"{r.engine_action}{klass}"
        if r.gate_excluded:
            pass_str = "SKIP (hardening)"
        elif r.pending_l3:
            pass_str = "OK (L3-pending)"
        else:
            pass_str = "OK" if r.passed else "**FAIL**"
        lines.append(
            f"| {r.case_id} | {r.label[:60]} | {phase_tag} | {r.must} | {engine_str} | {pass_str} |"
        )

    # Hardening summary.
    hs = scorecard.get("hardening_summary", {})
    if hs.get("cases"):
        lines.append("")
        lines.append("## Hardening Fixtures (pre-hardening baseline)\n")
        lines.append(
            f"These {hs['cases']} fixtures encode POST-hardening expected semantics. "
            "They fail on the current engine — that is the point; they document the target.\n"
        )
        if hs.get("failing_ids"):
            lines.append("**Failing on current engine:**\n")
            for fid in hs["failing_ids"]:
                lines.append(f"- `{fid}`")

    out_path.write_text("\n".join(lines) + "\n")
    print(f"  Markdown report written to: {out_path}")


# ── Entry point ────────────────────────────────────────────────────────────────


def run(
    baseline_path: Path | None = None,
    out_path: Path | None = None,
    llm_mode: bool = False,
    include_hardening: bool = False,
) -> dict:
    """Load fixtures, run engine, build scorecard, print table. Return scorecard."""
    cases = load_fixtures(include_hardening=include_hardening)
    if not cases:
        print("No fixtures found in", FIXTURES_DIR, file=sys.stderr)
        sys.exit(1)

    results: list[CaseResult] = []
    with _tempfile.TemporaryDirectory(prefix="onw-dedup-eval-case-") as tmp_root:
        for i, case in enumerate(cases):
            case_tmp = Path(tmp_root) / f"case_{i:03d}"
            case_tmp.mkdir()
            result = _evaluate_case(case, case_tmp, llm_mode=llm_mode)
            results.append(result)

    scorecard = _build_scorecard(results)
    _print_table(results, scorecard)

    if baseline_path and baseline_path.exists():
        baseline = json.loads(baseline_path.read_text())
        _print_delta(scorecard, baseline)

    REPORT_PATH.write_text(json.dumps(scorecard, indent=2))
    print(f"  Report written to: {REPORT_PATH}")

    if out_path:
        _write_markdown(results, scorecard, out_path)

    return scorecard


def main() -> None:
    baseline_path: Path | None = None
    out_path: Path | None = None
    llm_mode = False
    include_hardening = False

    args = sys.argv[1:]
    i = 0
    while i < len(args):
        arg = args[i]
        if arg == "--baseline" and i + 1 < len(args):
            baseline_path = Path(args[i + 1])
            i += 2
        elif arg == "--out" and i + 1 < len(args):
            out_path = Path(args[i + 1])
            i += 2
        elif arg == "--llm":
            llm_mode = True
            i += 1
        elif arg == "--include-hardening":
            include_hardening = True
            i += 1
        else:
            i += 1

    scorecard = run(
        baseline_path=baseline_path,
        out_path=out_path,
        llm_mode=llm_mode,
        include_hardening=include_hardening,
    )

    # Hard gate: false-merge rate on must-NOT set must be 0.
    if scorecard["false_merge_count"] > 0:
        print(
            f"GATE FAILED: {scorecard['false_merge_count']} false merge(s) on "
            "must-NOT-merge cases.",
            file=sys.stderr,
        )
        sys.exit(1)

    # Hard gate: all idempotency / exact cases (must=noop) must be noop.
    noop_failures = [
        r
        for r in scorecard["per_case"]
        if r["must"] == "noop"
        and not r["pending_l3"]
        and not r["gate_excluded"]
        and not r["passed"]
    ]
    if noop_failures:
        ids = ", ".join(r["id"] for r in noop_failures)
        print(f"GATE FAILED: idempotency cases failed: {ids}", file=sys.stderr)
        sys.exit(1)

    print("  Gate: PASS (false-merge-rate=0, all idempotency cases correct)")


if __name__ == "__main__":
    main()
