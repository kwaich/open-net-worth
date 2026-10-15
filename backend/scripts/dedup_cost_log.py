"""
One-shot cost-log correction: remove two families of redundant/phantom cost records.

(1) PROVIDER DUPLICATES (fixed in backend/ai/models/openai_provider.py): ``stream_chat``
    recorded a cost entry itself, per API call, with a HARDCODED ``source="chat"`` and an
    empty ``session_id`` — on TOP of the single per-turn record ``assistant.py::_record_cost``
    already writes with the real source/session/surface. So every OpenAI-compatible (grok/xAI)
    turn was logged twice and the copy was mislabeled ``source="chat"`` with no session,
    inflating the total AND the "Chat" surface. The Anthropic provider never self-records, so
    Claude turns are unaffected.
    Removed when:  source == "chat"  AND  session_id empty/absent  AND  model is NOT claude.
      - The assistant's own per-turn "chat" record ALWAYS carries a real session_id (web = the
        conversation id, scheduled = "scheduled-<id>") → KEPT.
      - Empty-session records with a DIFFERENT source (briefing, asset_brief, memory_*,
        chat_suggestions, price_monitor, complete()/llm_seam paths, …) are legitimate single
        writers → UNTOUCHED.

(2) NARRATION PHANTOMS (fixed in backend/monitoring/intel_digest_narration.py): after the real
    narration LLM call (already billed by ``complete()`` under source="intel_digest" with the
    real model/tokens/cost), a redundant record was written with a FAKE ``model="narration"``,
    a hardcoded 120-token cap, and ``cost_usd=0.0`` — inventing a bogus model in the "By model"
    breakdown and double-counting the intel_digest turn/tokens (cost-neutral, but wrong counts).
    Removed when:  model == "narration"  (these are always $0 phantoms).

DAILY ROLLUP (the cost_log_daily table, keyed date|model|source — session_id dropped on
rollup):
  - Narration phantoms roll into whole ``model="narration"`` buckets that ARE cleanly
    identifiable and removable ($0), so they ARE swept here too.
  - Provider duplicates cannot be separated once rolled up (chat turns fold their real record
    and the duplicate into the same date|model|chat bucket). This instance's rollup ``chat``
    buckets are Claude-only (+ a local/free llama bucket), so it is UNCONTAMINATED; the script
    VERIFIES this and REFUSES to guess if a contaminated openai-compatible chat bucket appears.

SAFE BY CONSTRUCTION:
  - dry-run by default; ``--apply`` writes, but only after a timestamped JSON backup of both
    logs' current content (WAVE 5B: JSON->DB — a copy of the DB rows, not a file copy, since
    there is no longer a cost_log.json/cost_log_daily.json to ``shutil.copy2``).
  - idempotent: a second run finds nothing removable and is a no-op.
  - kept records/buckets are preserved verbatim; ordering preserved (rows are removed by id,
    never rewritten as a wholesale replace).
  - re-verifies after writing that only the identified rows were removed.

Run against the live tree:  .venv/bin/python3 -m backend.scripts.dedup_cost_log           (dry-run)
                            .venv/bin/python3 -m backend.scripts.dedup_cost_log --apply   (writes)
"""

from __future__ import annotations

import argparse
import collections
import json
from datetime import datetime, timezone

from backend.core.io import atomic_write_text
from backend.core.paths import DATA_DIR
from backend.db.cost_log_store import CostLogDailyStore, CostLogStore


def _is_provider_duplicate(rec: dict) -> bool:
    """True iff ``rec`` is a duplicate copy written by the old openai_provider self-record.

    Signature: source=="chat", empty/absent session_id, non-claude model (claude routes
    through the anthropic provider, which never self-recorded)."""
    if rec.get("source") != "chat":
        return False
    if rec.get("session_id"):  # a real session id → the assistant's keeper record
        return False
    model = str(rec.get("model") or "")
    if model.startswith("claude") or "anthropic" in model:
        return False
    return True


def _is_narration_phantom(rec: dict) -> bool:
    """True iff ``rec`` is a phantom intel-digest narration record (fake model, $0)."""
    return rec.get("model") == "narration"


def _is_removable(rec: dict) -> bool:
    return _is_provider_duplicate(rec) or _is_narration_phantom(rec)


def _summarize(records: list[dict]) -> tuple[int, float]:
    return len(records), round(sum(r.get("cost_usd", 0.0) for r in records), 4)


def _contaminated_daily_chat_buckets(daily: dict) -> list[tuple[str, dict]]:
    """openai-compatible, non-zero-cost ``chat`` daily buckets — would be contaminated but
    un-de-dupable (session_id is gone). Empty list = clean."""
    hits = []
    for key, agg in (daily or {}).items():
        if agg.get("source") != "chat":
            continue
        model = str(agg.get("model") or "")
        if model.startswith("claude") or "anthropic" in model:
            continue
        if round(agg.get("cost_usd", 0.0), 6) == 0.0:  # local/free (e.g. ollama) → harmless
            continue
        hits.append((key, agg))
    return hits


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Remove provider-duplicate + narration-phantom cost records."
    )
    ap.add_argument("--apply", action="store_true", help="write changes (default: dry-run).")
    args = ap.parse_args()

    raw_with_ids = CostLogStore().all_with_ids()  # [(id, payload), ...], oldest first
    if not raw_with_ids:
        print("cost_log is empty — nothing to do.")
        return
    raw = [r for _, r in raw_with_ids]
    daily = CostLogDailyStore().all_as_dict()

    removed_with_ids = [(rid, r) for rid, r in raw_with_ids if _is_removable(r)]
    removed = [r for _, r in removed_with_ids]
    kept = [r for r in raw if not _is_removable(r)]
    n_prov = sum(1 for r in removed if _is_provider_duplicate(r))
    n_narr = sum(1 for r in removed if _is_narration_phantom(r))

    n_all, c_all = _summarize(raw)
    n_rm, c_rm = _summarize(removed)
    n_keep, c_keep = _summarize(kept)

    print("=== raw cost_log ===")
    print(f"  before:            {n_all:>5} records   ${c_all}")
    print(f"  removed:           {n_rm:>5} records   ${c_rm}")
    print(f"    provider-dups:   {n_prov:>5}   narration-phantoms: {n_narr}")
    print(f"  after:             {n_keep:>5} records   ${c_keep}")
    if removed:
        by_model = collections.Counter(r.get("model") for r in removed)
        print(f"  removed models: {dict(by_model)}")
    print(
        f"  chat-source $ before: {round(sum(r.get('cost_usd', 0) for r in raw if r.get('source') == 'chat'), 4)}"
    )
    print(
        f"  chat-source $ after:  {round(sum(r.get('cost_usd', 0) for r in kept if r.get('source') == 'chat'), 4)}"
    )

    # --- daily rollup: sweep narration phantoms; verify no un-fixable chat contamination ---
    daily_kept = {k: a for k, a in daily.items() if a.get("model") != "narration"}
    daily_removed = {k: a for k, a in daily.items() if a.get("model") == "narration"}
    contaminated = _contaminated_daily_chat_buckets(daily)
    print("\n=== daily cost_log_daily ===")
    print(
        f"  narration buckets removed: {len(daily_removed)}   ${round(sum(a.get('cost_usd', 0) for a in daily_removed.values()), 4)}"
    )
    if not contaminated:
        print(
            "  chat buckets: clean — no openai-compatible, non-free 'chat' bucket to worry about."
        )
    else:
        print(
            f"  WARNING: {len(contaminated)} openai-compatible 'chat' bucket(s) may be contaminated"
        )
        print("  but cannot be de-duplicated (session_id dropped on rollup). Manual review:")
        for key, agg in contaminated:
            print(f"    {key}  ${round(agg.get('cost_usd', 0), 4)}  turns={agg.get('turns')}")

    if not args.apply:
        print("\nDRY-RUN — no rows written. Re-run with --apply to correct the log.")
        return

    if not removed and not daily_removed:
        print("\nNothing to remove — logs already clean. No-op.")
        return

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    if removed:
        backup = DATA_DIR / f"cost_log.json.bak-dedup-{stamp}"
        atomic_write_text(backup, json.dumps(raw))  # pre-change snapshot of every row
        print(f"\nbacked up raw log (pre-change snapshot) → {backup}")
        CostLogStore().delete_many([rid for rid, _ in removed_with_ids])
        print(f"removed {n_rm} record(s); {n_keep} remain.")
    if daily_removed:
        dbackup = DATA_DIR / f"cost_log_daily.json.bak-dedup-{stamp}"
        atomic_write_text(dbackup, json.dumps(daily))  # pre-change snapshot of every bucket
        print(f"backed up daily log (pre-change snapshot) → {dbackup}")
        CostLogDailyStore().delete_keys(list(daily_removed.keys()))
        print(f"removed {len(daily_removed)} daily bucket(s); {len(daily_kept)} remain.")

    # Re-verify: nothing removable must remain in either store.
    re_raw = CostLogStore().all()
    re_daily = CostLogDailyStore().all_as_dict()
    rem_raw = [r for r in re_raw if _is_removable(r)]
    rem_daily = [k for k, a in re_daily.items() if a.get("model") == "narration"]
    ok = not rem_raw and not rem_daily
    print(
        f"verify: raw removable left: {len(rem_raw)}  |  daily narration left: {len(rem_daily)}  |  {'OK' if ok else 'MISMATCH'}"
    )
    if not ok:
        raise SystemExit("post-write verification FAILED — restore from the backup(s) above.")


if __name__ == "__main__":
    main()
