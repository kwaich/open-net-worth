"""Accuracy-evaluation harness for the statement-import matching/categorization pipeline
(``backend/services/statements/`` + ``backend/services/matching.py``).

Why this exists: hand-verifying a code change against a real statement by eyeballing
screenshots does not scale and does not prove anything ("fix a bug" -> "still wrong" ->
re-diagnose, with no objective signal). This script runs a directory of real statement
files through the SAME entry point the app uses (``decisions.decision_preview``) and
scores every row against a hand-authored "golden" answer file, so two runs (before/after a
code change) produce one comparable number instead of a screenshot.

USAGE
-----
    python3 -m backend.scripts.eval_import_accuracy --eval-dir ~/onw-eval
    python3 -m backend.scripts.eval_import_accuracy --eval-dir ~/onw-eval --statement "some_file.xlsx"
    python3 -m backend.scripts.eval_import_accuracy --eval-dir ~/onw-eval --out ~/onw-eval/results/run1.json

``--eval-dir`` (or the ``ONW_EVAL_DIR`` env var) must point at a directory OUTSIDE this
repo containing two subdirectories:
    <eval-dir>/statements/<filename>       — the real statement files
    <eval-dir>/golden/<filename-stem>.json — one hand-authored golden-answer file per statement

There is deliberately NO default — pointing this at anything inside the repo (or leaving
it unset) is a privacy bug waiting to happen, so it raises instead of guessing.

GOLDEN FILE SCHEMA (one per statement, matched by filename)
------------------------------------------------------------
    {
      "statement_file": "foo.xlsx",
      "account_id": "acc_..." | null,   # passed to decision_preview verbatim; null lets
                                         # the pipeline's own account auto-detection run
      "instructions": "..." | null,     # OPTIONAL owner guidance for this source, passed to
                                         # decision_preview as a ONE-OFF (never persisted).
                                         # Omitted/null -> the cloned profile store's
                                         # remembered instructions apply. See LUCIUS PASS.
      "notes": "free text",             # human comment ONLY — never sent to the pipeline
      "rows": [
        {
          "row_index": 0,               # 0-based — matches decision_preview's rows[].index
          "expected_kind": "expense" | "income" | "transfer" | null,
          "expected_category": "cat_..." | null,
          "expected_match_existing_id": "txn_..." | null,
          "expected_skip": true | false,
          "confidence": "high" | "medium" | "low"
        }, ...
      ],
      "coverage": {                     # OPTIONAL — the INVERTED view; see THE INVERTED VIEW
        "notes": "how the window was enumerated",
        "expected_uncovered": [         # must still be listed in "not on this statement"
          {"existing_id": "txn_...", "bucket": "rows"|"boundary"|"any", "reason": "..."}
        ],
        "expected_covered": [           # the statement accounts for these — must NOT be listed
          {"existing_id": "txn_...", "reason": "..."}
        ],
        "must_not_pair": [              # no row may claim this entry ("row_index" narrows it
          {"existing_id": "txn_...",    # to one row); a violation is a FALSE PAIRING
           "row_index": 12,             # (optional)
           "reason": "..."}
        ],
        "exhaustive_uncovered": true    # the author enumerated the WHOLE window, so any id on
      }                                 # the panel that is not named above is itself a failure
    }

A golden with no ``coverage`` block scores exactly as it did before that block existed — the
dimension is additive, never retroactive.

Scoring rule for the ``category``/``match`` dimensions (the two that are legitimately
"we don't know" for a real-world ambiguous row, e.g. a Grab charge that could be a ride
or a food order with identical statement text): a dimension is scored only when the
golden actually asserts something —
    * ``expected_X`` is a real value              -> always scored (exact match required)
    * ``expected_X`` is null AND confidence="high" -> scored as an explicit "must be
      unset" assertion (e.g. a transfer row must carry no category)
    * ``expected_X`` is null AND confidence is
      "medium"/"low"                               -> not scored (genuinely unverified)
This means confidence is not just cosmetic — it changes what gets graded, exactly so a
"low confidence, mismatch" row never reads the same as a "high confidence, mismatch" one.
A golden row's overall ``confidence`` often describes the KIND call specifically (e.g. "I'm
sure this is a transfer") without that same certainty extending to "and I've verified it
matches (or doesn't match) any specific existing ledger transaction" — an optional
``category_confidence`` / ``match_confidence`` per row overrides the row-level confidence
for just that one dimension's applicability check, so a well-verified kind doesn't
accidentally smuggle in an unverified "assert no match" as a side effect.

``kind`` and ``skip`` are always scored when the golden row itself is not
``expected_skip: true`` (a row we expect to be skipped only has its ``skip`` dimension
graded — everything else is moot for a row that never books).

THE INVERTED VIEW — WHY FOUR PER-ROW DIMENSIONS ARE NOT ENOUGH
---------------------------------------------------------------
All four dimensions above ask the same shape of question: *given this statement row, what did
the pipeline decide about it*. The import preview also answers the inverse —
``preview_ledger_check``'s "In your ledger, not on this statement" panel, which reports which
of the owner's EXISTING ledger entries this statement does and does not account for. Nothing
here scored that, and the cost was concrete: RFC ``2026-07-29-one-match-engine-instructions-
as-policy`` Phases 0-2 deleted an entire second matcher whose whole output landed on that
panel, and this harness reported byte-identical numbers before and after — 165/198 either way.
The owner's most visible real bug lived there and was invisible to all four.

``backend/scripts/eval_coverage.py`` adds two graded dimensions (``uncovered``, ``covered``)
and one deliberately UNGRADED count (``false_pairings`` — a row and a ledger entry asserted to
be the same transaction when they are not). A false pairing is a money-correctness defect: the
entry vanishes from the owner's panel and a merge would rewrite a real ledger row. It is never
averaged into an accuracy percentage, where one bad pairing in two hundred rows rounds to
99.5% and reads as fine — it is named, printed on its own line, and it FAILS THE RUN. Read
that module's docstring before authoring a ``coverage`` block.

WHERE "kind"/"category" COME FROM IN THE RESPONSE
--------------------------------------------------
``decision_preview``'s row payload does not carry a raw ``kind``/``category`` field
(that lives on the internal draft dict, which never rides into the API response) — it is
derived here from the same signals the UI itself reads: ``routed`` (SI-66 routing rule) or
``pair`` (fx-pair) or ``lucius.op == "route_transfer"`` all mean kind=transfer; otherwise
income/expense follows the row's own amount sign. Category comes straight from the row's
``enrich.category`` (the dry-run categorization verdict — rule > payee-default > hint).
"match" comes from ``duplicate_of`` (exact hash dedup) or the first matcher flag
(``near_duplicate``/``transfer_pair``/``transfer_leg_echo``) carrying a real ``existing_id``.

DATA ISOLATION
---------------
This script must NEVER touch the live ``~/.open-net-worth/data`` — it reuses this repo's
own ``backend.scripts.clone_data_for_qa`` (the same mechanism ``make qa-data`` uses) to
clone the live data dir into a fresh temp dir (secrets stripped) before any backend module
is imported, then points DATA_DIR/CACHE_DIR/LOGS_DIR at that clone under ONW_QA_MODE=1 —
running the REAL pipeline against realistic prior state (accounts, existing ledger
transactions to match against, learned statement-format profiles) without ever writing to
or reading secrets back out of the live instance. The clone is deleted when the run
finishes unless ``--keep-data-dir`` is passed.

clone_data_for_qa deliberately blanks every ``api_key``-shaped field (by design, for
handing a clone to a third-party QA/vision agent) — but this harness is a same-owner,
same-session offline eval, not a hand-off, and needs REAL LLM calls to fire under
whichever provider/model the owner already has configured (which is not necessarily
Anthropic — the API key resolution differs per provider and isn't worth re-deriving
generically here). The simplest correct fix: after cloning, this script copies the LIVE
(unscrubbed) ``model_config.json`` bytes over the clone's scrubbed copy — read-only
against the live file, written only into the throwaway clone, which is deleted when the
run finishes. This is a script the owner runs against their own instance, not a third
party's — reusing their own already-configured provider/model/key is the correct
behavior, not a leak.

THE LUCIUS PASS — WHY IT NEEDS ITS OWN REPORTING AND ITS OWN QUARANTINE
-----------------------------------------------------------------------
``lucius_pass.run_lucius_pass`` is the stage that turns the owner's standing notes for a
source into applied draft operations — ``route_transfer`` above all, which is the ONLY way
a row ever becomes ``kind=transfer`` short of a saved routing rule or an fx-pair. An eval
that cannot see whether it ran is blind to the most consequential stage in the pipeline,
so this harness reports it per statement (status, spend, and the proposed -> validated ->
applied funnel) and FAILS THE RUN when it was skipped on a statement whose golden expects
transfers.

Two independent things have to be true for the pass to do anything, and BOTH were silently
false in the 2026-07-29 baseline run:

1. EFFECTIVE INSTRUCTIONS must exist. They come from the source's format profile
   (``profiles.find_instructions``, keyed by header-row fingerprint) — which the data-dir
   clone below DOES carry — or from this golden's own ``instructions`` field, passed as a
   one-off. A source the owner never wrote notes for cannot be helped by this stage at all;
   the scorecard says so out loud rather than scoring it as a pipeline failure.

2. THE ROWS MUST STILL BE LIVE DRAFTS. ``_validate_ops`` drops every op aimed at a row that
   is skipped, has no draft, or is an L0 exact duplicate, and the prompt itself tells the
   model to leave "already imported" rows alone. Both are correct — an op on an already
   booked row would double-book on commit. But it means that previewing a statement whose
   own rows are ALREADY IN THE LEDGER measures nothing: every row dedups, every op is
   dropped, and ``kind`` collapses to the amount-sign fallback for the whole file. That is
   exactly what the first baseline measured — in the two wallet/bank exports the owner had
   already committed, every row and all but two rows respectively were duplicates — and it
   is why that run scored precisely the count of non-transfer rows and not one more.

Hence PRIOR-IMPORT QUARANTINE: before any preview, this harness looks up each statement's
own record in the clone (by file hash, the same key ``StatementStore`` dedups on), reads
its ``batch_ids``, and soft-deletes those transactions FROM THE CLONE through
``ledger_service.bulk_delete_transactions``. A user-deleted row frees its external-ref pair
by design (``import_common.known_external_ref_ids``), so the statement faces the ledger as
it stood before its own import — while every OTHER prior transaction (the hand-entered and
agent-booked rows the goldens expect matches against) stays exactly where it was. Pass
``--keep-prior-imports`` to skip this and measure the re-import case on purpose.

A corollary for GOLDEN AUTHORS, learned the hard way: ``expected_match_existing_id`` means
"this row should be recognized as a duplicate of a transaction that ALREADY EXISTED". It
must never name a transaction the statement's own import created — that assertion is
circular, only satisfiable on a re-import, and it silently contradicts the same golden's
own first-import ``expected_kind`` answers.

FORCE-REPARSE
--------------
Every statement is previewed with ``force_reparse=True``. Without it, a statement whose
statements.json record already carries cached ``import_rows``/``lucius_ops`` from a PRIOR
run would just replay that old cache — which defeats the entire point of an eval harness
meant to measure whether a CODE change helped, since the old cache was produced by
whatever code existed when it was cached. ``force_reparse`` guarantees every run actually
exercises the current code end to end (parse -> guards -> match -> Lucius pass -> enrich).
Column-schema memory (statement_formats.json, e.g. an xlsx's learned header mapping) is
untouched by this flag and stays in play — that is realistic user-facing memory, not
something this harness is trying to hold constant.

COST
-----
This pipeline makes real LLM calls (schema inference on a cold format, Lucius's
draft-review pass, category-hint suggestions) — running this eval costs real money on
every invocation. The scorecard prints total ``llm_cost_usd`` per statement and overall,
plus the parse-tier ``llm_calls`` count. Note that top-level ``llm_calls`` is PARSE TIER
ONLY — a zero there says nothing about whether the Lucius pass ran (a tabular-profile
source parses with zero LLM calls and can still spend several on the pass). The pass's own
``llm_calls``/``cost_usd`` are reported separately, under ``lucius_pass``.

DIFFING TWO RUNS
-----------------
``--out`` writes the full scorecard (overall + per-statement + every mismatch) as one
JSON file. To compare a before/after run: ``diff <(jq . before.json) <(jq . after.json)``,
or write a small follow-up script that loads both and prints the delta in each
dimension's accuracy — not built here since a single clear scorecard was the priority.
"""

from __future__ import annotations

# ── DATA SAFETY: resolve a real ANTHROPIC_API_KEY, then redirect every backend path to
# an isolated clone of the live data dir — BEFORE any other backend module is imported.
# Mirrors backend/scripts/dedup_eval.py's pattern; see the module docstring above.
import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]

sys.path.insert(0, str(_REPO_ROOT))
from backend.scripts.eval_coverage import (  # noqa: E402 — after the path bootstrap above
    COVERAGE_DIMENSIONS,
    print_coverage,
    score_coverage,
)


def _restore_model_config(dest: Path) -> None:
    """Overwrite the clone's scrubbed model_config.json with the LIVE one, unscrubbed —
    read-only against the live file; the copy lives only in the throwaway clone. See the
    module docstring's DATA ISOLATION section for why this is correct for THIS script."""
    live_config = Path.home() / ".open-net-worth" / "data" / "model_config.json"
    if live_config.exists():
        shutil.copy2(live_config, dest / "model_config.json")


def _isolate_data_dir(keep: bool) -> Path:
    """Clone the live DATA_DIR (secrets stripped) into a fresh temp dir via the repo's own
    clone_data_for_qa, then point DATA_DIR/CACHE_DIR/LOGS_DIR at it. Returns the clone path."""
    sys.path.insert(0, str(_REPO_ROOT))
    from backend.scripts import clone_data_for_qa  # no backend.core.paths import — safe

    source = clone_data_for_qa._resolve_source()
    dest = Path(tempfile.mkdtemp(prefix="onw-eval-data-"))
    clone_data_for_qa.clone(source, dest)
    _restore_model_config(dest)
    os.environ["DATA_DIR"] = str(dest)
    os.environ["CACHE_DIR"] = str(dest.parent / f"{dest.name}-cache")
    os.environ["LOGS_DIR"] = str(dest.parent / f"{dest.name}-logs")
    os.environ["ONW_QA_MODE"] = "1"
    os.environ["AUTH_DISABLED"] = "true"
    print(f"[eval] isolated DATA_DIR clone: {dest}{' (kept)' if keep else ''}", file=sys.stderr)

    # decision_preview calls through backend.core.llm_seam, which only has a provider
    # registered once backend.ai.tools has been imported (it wires the seam as an import
    # side effect — normally triggered by the app's own startup). This script never boots
    # the app, so it must trigger that registration itself, or every LLM-dependent step
    # (schema inference, the Lucius pass, category hints) silently no-ops with
    # LLMUnavailable swallowed several layers down.
    import backend.ai.tools  # noqa: F401

    return dest


def _parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--eval-dir", default=os.environ.get("ONW_EVAL_DIR"))
    ap.add_argument("--statement", help="only run this one statement (filename, e.g. foo.xlsx)")
    ap.add_argument(
        "--out", help="write the full scorecard JSON here (default: <eval-dir>/results/<ts>.json)"
    )
    ap.add_argument(
        "--keep-data-dir",
        action="store_true",
        help="don't delete the isolated DATA_DIR clone on exit",
    )
    ap.add_argument(
        "--force-relearn", action="store_true", help="pass force_relearn=True to decision_preview"
    )
    ap.add_argument(
        "--keep-prior-imports",
        action="store_true",
        help=(
            "don't quarantine each statement's own earlier import from the cloned ledger — "
            "measures the RE-IMPORT case, where every row dedups and no decision is reachable"
        ),
    )
    return ap.parse_args()


def _resolve_eval_dir(raw: str | None) -> Path:
    if not raw:
        raise SystemExit(
            "No --eval-dir given and ONW_EVAL_DIR is unset. This script reads real statement\n"
            "data and must never default to a path inside the repo — point it at a directory\n"
            "OUTSIDE the repo containing statements/ and golden/ subdirectories, e.g.:\n"
            "    python3 -m backend.scripts.eval_import_accuracy --eval-dir ~/onw-eval"
        )
    path = Path(raw).expanduser().resolve()
    try:
        path.relative_to(_REPO_ROOT)
    except ValueError:
        pass
    else:
        raise SystemExit(f"--eval-dir ({path}) resolves inside the repo ({_REPO_ROOT}) — refusing.")
    if not (path / "statements").is_dir() or not (path / "golden").is_dir():
        raise SystemExit(f"{path} must contain both a statements/ and a golden/ subdirectory.")
    return path


_FMT_BY_SUFFIX = {".xlsx": "xlsx", ".xls": "xlsx", ".csv": "csv", ".pdf": "pdf"}


def _fmt_for(path: Path) -> str:
    fmt = _FMT_BY_SUFFIX.get(path.suffix.lower())
    if not fmt:
        raise SystemExit(f"Unrecognized statement file extension: {path}")
    return fmt


# ── Prior-import quarantine ────────────────────────────────────────────────────────────


def quarantine_prior_imports(eval_dir: Path, goldens: list[dict]) -> dict[str, dict]:
    """Soft-delete, IN THE CLONE ONLY, every transaction each eval statement's OWN earlier
    import created — so the pipeline faces the ledger as it stood before that import.

    Without this the harness measures a re-import: every row hits L0 exact dedup, every
    Lucius op is dropped as "already booked", and ``kind`` degrades to the amount-sign
    fallback for the entire file. See the module docstring's LUCIUS PASS section.

    Runs for ALL statements before ANY preview, deliberately: two of these files are the two
    LEGS of the same real-world transfers, so quarantining lazily would leave one statement
    matching against transactions the other one's import created.

    Deletion goes through the ledger's own soft-delete choke — a user-deleted row frees its
    external-ref pair by design, which is precisely the "not imported yet" state wanted."""
    from backend.db.statement_store import StatementStore
    from backend.services import ledger as ledger_service

    store = StatementStore()
    out: dict[str, dict] = {}
    for golden in goldens:
        name = golden["statement_file"]
        path = eval_dir / "statements" / name
        # StatementStore keys its dedup on the sha256 of the file bytes (see its docstring's
        # `file_hash` field) — the same lookup decision_preview's own create() performs.
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        record = store.find_by_hash(digest)
        batch_ids = set((record or {}).get("batch_ids") or [])
        ids = [
            t["id"]
            for t in ledger_service.transactions.all()
            if t.get("batch_id") in batch_ids and not t.get("deleted")
        ]
        if ids:
            ledger_service.bulk_delete_transactions(ids)
        out[name] = {
            "statement_record_id": (record or {}).get("id"),
            "batch_ids": sorted(batch_ids),
            "quarantined_txns": len(ids),
        }
        print(
            f"[eval] {name}: quarantined {len(ids)} txn(s) from "
            f"{len(batch_ids)} prior import batch(es)",
            file=sys.stderr,
        )
    return out


# ── Scoring ────────────────────────────────────────────────────────────────────────────

_MATCH_FLAG_TYPES = ("near_duplicate", "transfer_pair", "transfer_leg_echo")
ROW_DIMENSIONS = ("skip", "kind", "category", "match")
DIMENSIONS = ROW_DIMENSIONS + COVERAGE_DIMENSIONS


def _actual_kind(entry: dict) -> str | None:
    if entry.get("skip"):
        return None
    if entry.get("routed") is not None or entry.get("pair") is not None:
        return "transfer"
    lucius = entry.get("lucius") or {}
    if lucius.get("op") == "route_transfer":
        return "transfer"
    return "income" if (entry.get("amount") or 0) > 0 else "expense"


def _actual_category(entry: dict) -> str | None:
    return (entry.get("enrich") or {}).get("category")


def _actual_match_id(entry: dict) -> str | None:
    if entry.get("duplicate") and entry.get("duplicate_of"):
        return entry["duplicate_of"]
    for flag in entry.get("flags") or []:
        if flag.get("type") in _MATCH_FLAG_TYPES and flag.get("existing_id"):
            return flag["existing_id"]
    return None


def _dimension_applicable(expected, confidence: str) -> bool:
    """A None expectation is only a real assertion ("must be unset") at confidence=high;
    at medium/low it means "genuinely unverified" and the dimension is skipped."""
    return expected is not None or confidence == "high"


def score_statement(golden: dict, response: dict) -> dict:
    by_index = {r["index"]: r for r in response.get("rows") or []}
    row_scores: list[dict] = []
    mismatches: list[dict] = []
    dim_totals = dict.fromkeys(DIMENSIONS, 0)
    dim_correct = dict.fromkeys(DIMENSIONS, 0)

    for grow in golden["rows"]:
        idx = grow["row_index"]
        confidence = grow.get("confidence", "medium")
        entry = by_index.get(idx)
        row_ok = True

        if entry is None:
            mismatches.append(
                {
                    "row_index": idx,
                    "field": "row_missing",
                    "expected": "a row at this index",
                    "actual": "no row returned at this index (row count likely changed)",
                    "confidence": confidence,
                }
            )
            row_scores.append({"row_index": idx, "ok": False})
            continue

        dim_totals["skip"] += 1
        actual_skip = bool(entry.get("skip"))
        if actual_skip == bool(grow["expected_skip"]):
            dim_correct["skip"] += 1
        else:
            row_ok = False
            mismatches.append(
                {
                    "row_index": idx,
                    "field": "skip",
                    "expected": grow["expected_skip"],
                    "actual": actual_skip,
                    "confidence": confidence,
                }
            )

        if grow["expected_skip"]:
            row_scores.append({"row_index": idx, "ok": row_ok})
            continue

        dim_totals["kind"] += 1
        actual_kind = _actual_kind(entry)
        if actual_kind == grow.get("expected_kind"):
            dim_correct["kind"] += 1
        else:
            row_ok = False
            mismatches.append(
                {
                    "row_index": idx,
                    "field": "kind",
                    "expected": grow.get("expected_kind"),
                    "actual": actual_kind,
                    "confidence": confidence,
                }
            )

        expected_cat = grow.get("expected_category")
        if _dimension_applicable(expected_cat, grow.get("category_confidence", confidence)):
            dim_totals["category"] += 1
            actual_cat = _actual_category(entry)
            if actual_cat == expected_cat:
                dim_correct["category"] += 1
            else:
                row_ok = False
                mismatches.append(
                    {
                        "row_index": idx,
                        "field": "category",
                        "expected": expected_cat,
                        "actual": actual_cat,
                        "confidence": confidence,
                    }
                )

        expected_match = grow.get("expected_match_existing_id")
        if _dimension_applicable(expected_match, grow.get("match_confidence", confidence)):
            dim_totals["match"] += 1
            actual_match = _actual_match_id(entry)
            if actual_match == expected_match:
                dim_correct["match"] += 1
            else:
                row_ok = False
                mismatches.append(
                    {
                        "row_index": idx,
                        "field": "match_existing_id",
                        "expected": expected_match,
                        "actual": actual_match,
                        "confidence": confidence,
                    }
                )

        row_scores.append({"row_index": idx, "ok": row_ok})

    # The inverted view (ledger -> statement). Scored beside the row dimensions, never inside
    # `row_accuracy`: its unit is a LEDGER ENTRY, not a statement row, so adding it to a
    # per-row ratio would silently change what that ratio means.
    coverage = score_coverage(golden, response)
    for dim in COVERAGE_DIMENSIONS:
        dim_totals[dim] += coverage["dimension_totals"][dim]
        dim_correct[dim] += coverage["dimension_correct"][dim]

    n_rows = len(row_scores)
    n_correct = sum(1 for r in row_scores if r["ok"])
    dim_accuracy = {
        dim: (dim_correct[dim] / dim_totals[dim] if dim_totals[dim] else None) for dim in DIMENSIONS
    }
    return {
        "row_accuracy": n_correct / n_rows if n_rows else None,
        "rows_total": n_rows,
        "rows_correct": n_correct,
        "dimension_totals": dim_totals,
        "dimension_correct": dim_correct,
        "dimension_accuracy": dim_accuracy,
        "mismatches": mismatches,
        "coverage": coverage,
    }


# ── Runner ─────────────────────────────────────────────────────────────────────────────


def _pass_verdict(golden: dict, response: dict) -> dict:
    """Whether the Lucius pass ran, what it cost, and where its decisions went — plus the
    BLOCKING check that this harness exists to keep honest.

    ``blocking`` is the recurrence guard for the 2026-07-29 bug: guidance reached the
    pipeline, the golden says this statement contains transfers (which nothing but the pass
    can produce here), and the pass still did not run. A silently-skipped pass is precisely
    the failure that made a whole baseline meaningless, so it fails the run rather than
    quietly scoring 0 on every transfer row.

    ``warning`` is the DIFFERENT, honest case: the owner never saved notes for this source,
    so the pass has nothing to act on. That is a gap in the owner's setup, not a pipeline
    regression, and it must not be laundered into either a pass or a hard failure."""
    lp = response.get("lucius_pass") or {}
    instructions = response.get("instructions") or {}
    chars = len(instructions.get("text") or "")
    wants_transfer = any(
        r.get("expected_kind") == "transfer" and not r.get("expected_skip") for r in golden["rows"]
    )
    status = lp.get("status") or ("skipped" if not chars else "missing")
    blocking = warning = None
    if wants_transfer and chars and status != "ran":
        blocking = (
            f"{chars} chars of owner guidance reached the pipeline and the golden expects "
            f"transfers, but the Lucius pass status is {status!r} — nothing could route."
        )
    elif status == "unavailable":
        blocking = "the Lucius pass's provider failed mid-run; this statement measured nothing."
    elif wants_transfer and not chars:
        warning = (
            "the golden expects transfers but this source has NO owner instructions (none "
            "saved on its format profile, none in the golden's `instructions` field) — the "
            "pass cannot run, so every transfer row is unreachable by design, not by defect."
        )
    return {
        "status": status,
        "instructions_chars": chars,
        "instructions_source": (
            "golden" if chars and not instructions.get("remembered") else "profile-memory"
        )
        if chars
        else "none",
        "proposed": lp.get("proposed"),
        "validated": lp.get("validated"),
        "applied": lp.get("applied"),
        "routes": lp.get("routes"),
        "categories": lp.get("categories"),
        "skips": lp.get("skips"),
        "rejected_matches": lp.get("rejected_matches"),
        "forced_matches": lp.get("forced_matches"),
        "llm_calls": lp.get("llm_calls"),
        "cost_usd": lp.get("cost_usd"),
        "golden_expects_transfers": wants_transfer,
        "blocking": blocking,
        "warning": warning,
    }


def run_one(eval_dir: Path, golden_path: Path, *, force_relearn: bool) -> dict:
    from backend.services.statements.decisions import decision_preview

    golden = json.loads(golden_path.read_text(encoding="utf-8"))
    stmt_path = eval_dir / "statements" / golden["statement_file"]
    if not stmt_path.exists():
        raise SystemExit(f"golden file {golden_path.name} references missing statement {stmt_path}")

    content = stmt_path.read_bytes()
    fmt = _fmt_for(stmt_path)
    try:
        response = decision_preview(
            content,
            fmt,
            golden.get("account_id"),
            filename=stmt_path.name,
            # The golden's own guidance wins when it has any; otherwise this resolves to the
            # source's remembered instructions inside decision_preview. Never persisted
            # either way — an eval must not teach the (throwaway, but shared-across-
            # statements) profile store something the next statement then inherits.
            instructions=(golden.get("instructions") or "").strip() or None,
            remember_instructions=False,
            force_reparse=True,
            force_relearn=force_relearn,
        )
    except Exception as exc:  # noqa: BLE001 — one statement's failure must not sink the run
        return {
            "statement_file": golden["statement_file"],
            "error": f"{type(exc).__name__}: {exc}",
        }

    rows = response.get("rows") or []
    score = score_statement(golden, response)
    return {
        "statement_file": golden["statement_file"],
        "resolved_account_id": response.get("resolved_account_id"),
        "needs_account": response.get("needs_account"),
        "tier": response.get("tier"),
        "llm_calls": response.get("llm_calls"),
        "llm_cost_usd": response.get("llm_cost_usd"),
        "lucius_pass": _pass_verdict(golden, response),
        # The state the pass actually saw. A file whose rows are all duplicates cannot be
        # decided at all, whatever the pass proposes — surfaced so that failure mode can
        # never again hide behind a plausible-looking accuracy number.
        "rows_duplicate": sum(1 for r in rows if r.get("duplicate")),
        "rows_skipped": sum(1 for r in rows if r.get("skip")),
        "rows_live_draft": sum(1 for r in rows if not r.get("skip") and not r.get("duplicate")),
        **score,
    }


def _print_pass_line(lp: dict) -> None:
    """The Lucius-pass block: whether it ran, what it spent, and the proposed -> validated
    -> applied funnel. The funnel is printed as three numbers on purpose — an op can die at
    hard validation or at the contextual appliers, and collapsing that into one "applied"
    count is how a night of silently-dropped decisions happens."""
    src = lp["instructions_source"]
    print(
        f"  lucius pass: status={lp['status']} instructions={lp['instructions_chars']}ch ({src}) "
        f"calls={lp['llm_calls']} cost=${(lp['cost_usd'] or 0.0):.4f}"
    )
    if lp["status"] in ("ran", "cached"):
        print(
            f"    ops: proposed={lp['proposed']} -> validated={lp['validated']} -> "
            f"applied={lp['applied']}  "
            f"(routes={lp['routes']} categories={lp['categories']} skips={lp['skips']} "
            f"reject_match={lp['rejected_matches']} force_match={lp['forced_matches']})"
        )
    if lp["blocking"]:
        print(f"    ** FAIL: {lp['blocking']}")
    if lp["warning"]:
        print(f"    ** WARNING: {lp['warning']}")


def _print_false_pairings(scored: list[dict]) -> list[dict]:
    """The OVERALL false-pairing block. Deliberately its own section with its own count and no
    denominator: a wrong "these are the same transaction" claim removes a real ledger entry
    from the owner's not-on-statement panel and authorizes a merge that rewrites it, so it must
    never be able to average away inside a percentage. Returns every violation found."""
    every = [
        {**v, "statement_file": r["statement_file"]}
        for r in scored
        for v in r["coverage"]["false_pairings"]
    ]
    print("\n  FALSE PAIRINGS")
    if not every:
        print("    0 — no statement row claims a ledger entry it must not")
        return every
    print(f"    {len(every)} — each one is a money-correctness defect, not a missed convenience")
    for v in every:
        origin = "golden" if v["declared"] else "structural"
        print(
            f"    FAIL {v['statement_file']} [{origin}] row {v['row_index']} <-> "
            f"{v['existing_id']}: {v['why']}"
        )
    return every


def _print_scorecard(results: list[dict]) -> tuple[list[dict], list[dict]]:
    """Prints the scorecard; returns (Lucius-pass failures, false pairings)."""
    print("\n" + "=" * 88)
    print("STATEMENT-IMPORT ACCURACY SCORECARD")
    print("=" * 88)

    total_cost = 0.0
    total_calls = 0
    overall_totals = dict.fromkeys(DIMENSIONS, 0)
    overall_correct = dict.fromkeys(DIMENSIONS, 0)
    overall_rows = 0
    overall_rows_correct = 0

    for r in results:
        print(f"\n--- {r['statement_file']} ---")
        if "error" in r:
            print(f"  ERROR: {r['error']}")
            continue
        total_cost += r["llm_cost_usd"] or 0.0
        total_calls += r["llm_calls"] or 0
        pct = f"{r['row_accuracy'] * 100:.1f}%" if r["row_accuracy"] is not None else "n/a"
        print(f"  row accuracy: {r['rows_correct']}/{r['rows_total']} ({pct})")
        print(
            f"  resolved_account_id={r['resolved_account_id']} needs_account={r['needs_account']} tier={r['tier']}"
        )
        print(f"  llm_calls(parse-tier)={r['llm_calls']}  llm_cost_usd=${r['llm_cost_usd']:.4f}")
        print(
            f"  rows: {r['rows_live_draft']} live draft / {r['rows_duplicate']} duplicate "
            f"/ {r['rows_skipped']} skipped"
        )
        _print_pass_line(r["lucius_pass"])
        print_coverage(r["coverage"])
        for dim in DIMENSIONS:
            total = r["dimension_totals"][dim]
            correct = r["dimension_correct"][dim]
            dpct = f"{correct / total * 100:.1f}%" if total else "n/a"
            print(f"    {dim:<10} {correct}/{total} ({dpct})")
            overall_totals[dim] += total
            overall_correct[dim] += correct
        overall_rows += r["rows_total"]
        overall_rows_correct += r["rows_correct"]
        if r["mismatches"]:
            print(f"  mismatches ({len(r['mismatches'])}):")
            for m in r["mismatches"]:
                print(
                    f"    [row {m['row_index']}] {m['field']}: expected={m['expected']!r} "
                    f"actual={m['actual']!r}  (golden confidence={m['confidence']})"
                )

    print("\n" + "-" * 88)
    print("OVERALL")
    overall_pct = f"{overall_rows_correct / overall_rows * 100:.1f}%" if overall_rows else "n/a"
    print(f"  row accuracy: {overall_rows_correct}/{overall_rows} ({overall_pct})")
    for dim in DIMENSIONS:
        total = overall_totals[dim]
        correct = overall_correct[dim]
        dpct = f"{correct / total * 100:.1f}%" if total else "n/a"
        print(f"    {dim:<10} {correct}/{total} ({dpct})")
    print(f"\n  TOTAL llm_calls(parse-tier)={total_calls}  TOTAL llm_cost_usd=${total_cost:.4f}")

    scored = [r for r in results if "error" not in r]
    ran = [r for r in scored if r["lucius_pass"]["status"] in ("ran", "cached")]
    print("\n  LUCIUS PASS")
    print(f"    ran on {len(ran)}/{len(scored)} scored statement(s)")
    print(
        f"    ops: proposed={sum(r['lucius_pass']['proposed'] or 0 for r in ran)} -> "
        f"validated={sum(r['lucius_pass']['validated'] or 0 for r in ran)} -> "
        f"applied={sum(r['lucius_pass']['applied'] or 0 for r in ran)}"
    )
    print(
        f"    spend: calls={sum(r['lucius_pass']['llm_calls'] or 0 for r in ran)} "
        f"cost=${sum(r['lucius_pass']['cost_usd'] or 0.0 for r in ran):.4f}"
    )
    for r in scored:
        if r["lucius_pass"]["warning"]:
            print(f"    WARNING {r['statement_file']}: {r['lucius_pass']['warning']}")
    failures = [r for r in scored if r["lucius_pass"]["blocking"]]
    for r in failures:
        print(f"    FAIL {r['statement_file']}: {r['lucius_pass']['blocking']}")
    false_pairings = _print_false_pairings(scored)
    print("=" * 88 + "\n")
    return failures, false_pairings


def main() -> None:
    args = _parse_args()
    eval_dir = _resolve_eval_dir(args.eval_dir)
    data_dir = _isolate_data_dir(args.keep_data_dir)
    try:
        golden_paths = sorted((eval_dir / "golden").glob("*.json"))
        if args.statement:
            golden_paths = [
                p
                for p in golden_paths
                if json.loads(p.read_text()).get("statement_file") == args.statement
            ]
            if not golden_paths:
                raise SystemExit(f"no golden file references statement {args.statement!r}")

        goldens = [json.loads(p.read_text(encoding="utf-8")) for p in golden_paths]
        quarantine = {} if args.keep_prior_imports else quarantine_prior_imports(eval_dir, goldens)
        results = [run_one(eval_dir, p, force_relearn=args.force_relearn) for p in golden_paths]
        for r in results:
            r["prior_import_quarantine"] = quarantine.get(r["statement_file"])
        failures, false_pairings = _print_scorecard(results)

        out_path = (
            Path(args.out).expanduser()
            if args.out
            else eval_dir
            / "results"
            / f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
        )
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            json.dumps(
                {
                    "generated_at": datetime.now(timezone.utc).isoformat(),
                    "prior_import_quarantine": not args.keep_prior_imports,
                    "false_pairings": false_pairings,
                    "results": results,
                },
                indent=2,
            )
        )
        print(f"scorecard written to {out_path}", file=sys.stderr)
        if false_pairings:
            # A false pairing is not a scoring nuance — it is the pipeline claiming a row and a
            # real ledger entry are the same money when they are not. Non-zero exit, ahead of
            # the Lucius check, so no run carrying one can be read as a clean baseline.
            raise SystemExit(
                f"{len(false_pairings)} FALSE PAIRING(S) — see the scorecard. A statement row "
                "claims a ledger entry it must not."
            )
        if failures:
            # A run where the Lucius pass never fired on a statement it was needed for has
            # measured nothing about the stage that matters most — exit non-zero so it can
            # never be mistaken for a baseline.
            raise SystemExit(
                f"{len(failures)} statement(s) failed the Lucius-pass check — see the scorecard."
            )
    finally:
        if not args.keep_data_dir:
            shutil.rmtree(data_dir, ignore_errors=True)
            shutil.rmtree(os.environ.get("CACHE_DIR", ""), ignore_errors=True)
            shutil.rmtree(os.environ.get("LOGS_DIR", ""), ignore_errors=True)


if __name__ == "__main__":
    main()
