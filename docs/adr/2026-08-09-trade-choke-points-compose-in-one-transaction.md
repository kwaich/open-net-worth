---
title: Investment trade/vest/option chokes compose through locked_unit_of_work — the 9 dispose/acquire/vest/option compositions, the vest lot-mint deadlock guard, the create_transaction/bulk_delete_transactions conn= additions, and the documented _publish-on-rollback minor
status: Accepted
date: 2026-08-09
deciders: Claude (implementer agents, storage plan 4b tasks 4/7 — choke seam, rehearsal), repo owner (money-parity ruling — realized-event txn_id retrofit and the publish-firing point stay OUT of scope this wave)
---

# 2026-08-09. Investment trade/vest/option chokes compose through `locked_unit_of_work`

## Context

`JsonStore` never had cross-store atomicity. Plan 3 closed this for the money core's own
multi-store chokes, and Plan 4a (planning) reused the identical seam for its own multi-table
writes (ADRs `2026-08-08-money-core-choke-points-become-one-transaction.md`,
`2026-08-08-planning-choke-points-compose-in-one-transaction.md`). Investments had the SAME gap
on writes that are, if anything, more consequential to get right: a disposal that consumes lots,
appends a realized event, and books a proceeds cash leg; a vest that mints a lot, decrements a
locked position, and books an income leg — each previously a sequence of independent,
non-atomic writes that a crash mid-sequence could leave half-done, silently misstating either the
lot ledger or realized P&L. Two things were already true before this task started:

- `locked_unit_of_work` already existed, proven deadlock-free by money's own two-thread
  opposite-order test and reused unmodified by planning — investments needed to REUSE it, not
  reinvent it, and to extend the SAME global-alphabetical-lock-ordering discipline to whichever
  new tables it now touches.
- A known hazard from Wave A's own task 5 (the planning choke seam): a composed choke that calls
  back into ANOTHER top-level choke while already holding that choke's own table locks
  self-deadlocks (`threading.Lock` is not reentrant). Any investment choke whose write path calls
  a *different* top-level choke internally (a vest's FMV-lot mint calling the lot-acquire choke,
  say) needed to be identified and broken BEFORE composition, not discovered by a hang in
  production.

## Decision

We will:

1. **Nine investment writes now compose through `locked_unit_of_work`** — seven named by the
   task brief, two found by a sweep of every write path that touches more than one investment (or
   investment + money-core) table:

   | # | Choke | Tables locked |
   |---|---|---|
   | 1 | `lots._apply_acquire` | `lots`, `positions` (no cash leg exists for a buy anywhere in this codebase — `transactions` is deliberately NOT locked here, a documented deviation from the brief's literal 3-table sketch) |
   | 2 | `lots._apply_dispose` | `lots`, `positions`, `realized_events`, `transactions`, `txn_audit` |
   | 3 | `lots._undo_dispose` | same 5-table set as `_apply_dispose` |
   | 4 | `lots._delete_lot` | `lots`, `positions` (sweep finding — not in the brief's literal list) |
   | 5 | `equity._vest_apply` | `grants`, `lots`, `positions`, `transactions`, `vest_events`, `txn_audit` |
   | 6 | `equity._vest_undo` | same 6-table set as `_vest_apply` |
   | 7 | `equity_release.apply_release` | `lots`, `positions`, `vest_events` (sweep finding) |
   | 8 | `options._apply_open` | `option_contracts`, `transactions`, `txn_audit` |
   | 9 | `options._apply_close` | same 3-table set as `_apply_open` |

   `txn_audit` is locked in every set that calls `create_transaction`/`bulk_delete_transactions`
   internally — beyond the brief's literal table lists, but load-bearing: those two functions,
   when composed via `conn=`, write BOTH `transactions` AND `txn_audit` as one unit, so omitting
   `txn_audit`'s lock would leave its row-insert unprotected against a concurrent solo writer —
   exactly the anti-pattern `locked_unit_of_work`'s own docstring warns against. Verified
   deadlock-safe: `txn_audit` sorts alphabetically AFTER every other table in every one of these
   sets, matching `create_transaction`'s own pre-existing internal acquisition order, so no
   ordering inversion is introduced. `positions.apply_position_change`,
   `options._apply_expire`/`_apply_update`/`_apply_delete`, every grant-only op, and every
   watchlist/asset_brief write are deliberately left single-table and unwrapped — genuinely one
   table, so composing them would take a lock for zero atomicity benefit.
2. **The vest lot-mint deadlock guard is the wave's central risk, found and closed before it
   could hang production.** `_vest_apply`'s FMV-lot mint used to call
   `equity_release.create_vest_lot` → `lots_service.apply_lot_change({"type": "acquire", ...})` —
   the TOP-LEVEL choke, which (post this task) opens its OWN `locked_unit_of_work(lots,
   positions)`. Called from inside `_vest_apply`'s own `locked_unit_of_work(grants, lots,
   positions, ...)`, on the SAME thread, that second call would try to re-acquire locks it
   already holds — an immediate, silent, forever hang (`threading.Lock` is not reentrant; no
   exception, no timeout). Fixed by extraction, not by a workaround at the call site:
   `lots._mint_lot(lot_data, *, conn=None) -> tuple[dict, dict]` pulled out of
   `_apply_acquire`'s tail (`_make_lot` → `LotStore().insert` → `resync_position_aggregates`) —
   `_apply_acquire` now calls it too, no duplication. `equity_release.create_vest_lot` gained an
   additive `conn=` that, when given, branches to a new private `_mint_vest_lot`, replicating
   `_apply_acquire`'s fx_rate/base_currency historical-rate stamp (ADR `2026-07-15`) and calling
   `lots_service._mint_lot` DIRECTLY — never touching `apply_lot_change`. The identical pattern
   closes the delete side (`lots._delete_lot_core`, extracted from `_delete_lot`, called directly
   by `_vest_undo`) and the "shell position" resolution (`equity_release.park_pending` split into
   a standalone `resolve_pending_position` — called BEFORE the transaction opens — and a
   composable `apply_park_pending(..., conn=None)` tail). `conn=None` at every pre-existing call
   site is byte-identical to before. Verified via bounded-thread tests (`_run_bounded`, a 10s
   `join(timeout=)`) — a regression here hangs the TEST in ≤10 seconds, never the whole suite.
3. **`ledger_service.create_transaction`/`bulk_delete_transactions` gained `conn=` — a
   prerequisite this task genuinely needed, not a pre-existing capability.** The task brief
   assumed these were already `conn=`-capable from Plan 3; verified against the real code and
   found FALSE — at this wave's start, both functions always opened their OWN internal
   `locked_unit_of_work(transactions, txn_audit)`, with no way for a caller to compose them into
   a LARGER transaction. Added additively (a `_txn_unit_of_work` helper mirroring
   `PayloadTableStore._use_conn`'s existing pattern); `conn=None` is byte-identical to every
   pre-existing call site. This is a money-core write path change made FOR an investments-wave
   task — flagged here explicitly because it is the one place this wave's atomicity work reaches
   back into Plan 3's own choke points, not because the change itself does anything beyond
   thread an existing pattern one layer further.
4. **`resync_position_aggregates` needed conn threading beyond the brief's one-line sketch.**
   Empirically verified (a small script against the real engine, not assumed from documentation):
   a conn-less read issued from INSIDE an open `locked_unit_of_work` transaction sees the
   PRE-write state — a fresh connection cannot see another connection's uncommitted rows. Since
   `resync_position_aggregates` is called immediately after `bulk_consume`/`bulk_restore`/a lot
   insert in the SAME still-open transaction (composition #2/#3 above), a conn-less internal read
   would have silently computed a STALE aggregate — the position's `quantity`/`cost_basis` would
   reflect the pre-dispose state. Fixed by adding `conn=` to `PayloadTableStore.all()` (the base
   class every store inherits) and threading it through `LotStore.lots_for_position`/
   `open_lots_for_position` and the mixed-currency branch's `PositionStore`/`InstrumentStore`
   reads inside `resync_position_aggregates`.
5. **Two things are deliberately, explicitly NOT changed this wave — a money-parity ruling, not
   an oversight.** `_maybe_deposit_proceeds`'s auto-created proceeds transaction is never
   retroactively linked onto the realized event's own `txn_id` field (only a caller-supplied
   `change["txn_id"]` ever populates it) — a genuinely useful fix, but a computed-value/behavior
   change outside this task's atomicity-only scope, left untouched and documented inline.
   `create_transaction`'s `_publish_committed_transaction_events` still fires at the same
   relative point even when composed via an external `conn` — technically pre-commit from the
   OUTER transaction's point of view, so a rollback COULD publish an event for a never-committed
   transaction. This is rollback-path-only, best-effort, exception-guarded, file-outbox-based,
   and strictly SAFER than the pre-this-wave status quo (where a failed later step left the
   transaction permanently committed with ZERO rollback at all) — documented inline as a narrow,
   accepted risk rather than silently left for the next reader to rediscover.

**Verified at production scale (2026-08-09 dress rehearsal 4B):** the `/rewind` proof is a
direct, real-data exercise of composition #2 (`lots._apply_dispose`) and the shared `write_log`
seam decision 1 depends on. On the migrated clone, one `begin_turn`/`end_turn` pair wrapped a
real, non-taxable partial disposal (`lots_service.apply_lot_change({"type": "dispose", ...})`)
against an existing multi-lot position — writing `lots` (consumed quantity), `positions`
(resynced aggregate), and `realized_events` (the new event row) as ONE transaction. All three
tables' full row-image hashes were captured independently (a raw `SELECT`, not the checkpoint's
own bookkeeping) before the edit, confirmed CHANGED after the edit (proving the write took real
effect, not a no-op), then reverted via `ai.checkpoint.restore(checkpoint_id, mode="data")` — all
three tables byte-identical (sha256 match) to their pre-edit capture. A follow-up `net_worth()`
recomputation after the full dispose-then-restore cycle matched the pre-cycle figure exactly.
This proves the SAME row-level `write_log` mechanism money's and planning's own rehearsals
proved now covers a trade choke spanning three investment money-fact tables in one turn, with no
investment-specific gap. Full mechanism and evidence:
`.superpowers/sdd/2026-08-08-storage-plan4b-investments/task-7-report.md`.

## Consequences

### Positive

- Every investment choke that genuinely writes ≥2 wave tables now shares the money core's exact
  atomicity guarantee — proven by the SAME class of two-thread/bounded-timeout deadlock-free
  tests (`tests/services/test_trade_choke_atomicity.py`, 28 tests), not a new, weaker mechanism.
- The vest lot-mint deadlock guard is a real hardening found BEFORE it could hang a self-hoster's
  first vest apply — the extraction pattern (`_mint_lot`/`_delete_lot_core`,
  `resolve_pending_position`/`apply_park_pending`) is now the template for any future choke that
  needs to reuse another choke's write logic without re-entering the choke itself.
- The `create_transaction`/`bulk_delete_transactions` `conn=` additions are a real hardening of
  the shared money-core write path, not a one-off patch — any future choke that needs to compose
  a transaction leg into a larger investment (or any other) transaction inherits it.
- Two previously-silent correctness gaps improved as a side effect: `options._apply_open`'s
  failed premium leg used to leave an ORPHANED open contract with no leg (now rolls back
  together); `options._apply_close`'s exception handling was verified to roll back BEFORE
  `update_contract` ever runs, preserving the "contract stays open and retryable on a failed
  close leg" contract with a real rollback instead of "the next statement never happened to run."

### Negative

- `locked_unit_of_work`'s global-alphabetical-lock-ordering contract remains a discipline, not a
  compiler-enforced rule (the same caveat money's and planning's own ADRs already record) — a
  future investment choke that composes writes WITHOUT going through this seam can silently
  reintroduce the deadlock class it exists to close.
- The two deliberately-unchanged items (decision 5) are REAL, if narrow and documented,
  inconsistency surfaces: a realized event's `txn_id` can under-link its own auto-deposited
  proceeds leg, and a rollback can theoretically publish an event for a transaction that never
  committed. Accepted as out-of-scope for an atomicity-only task, not eliminated.
- `lane/inv-trade-atomicity` (a concurrent worktree) also restructures `backend/services/lots.py`
  — the new `_mint_lot`/`_delete_lot_core` helpers, the `_apply_acquire`/`_apply_dispose`/
  `_undo_dispose`/`_delete_lot` bodies, and `resync_position_aggregates`'s new `conn=` are all
  inside that lane's own likely change surface. A mechanical merge WILL conflict on function
  bodies, not just import lines; whoever integrates the two lanes must diff both change sets
  directly and pick (or combine) ONE composition design per function, not silently keep both.

### Follow-ups

- Forward-merge `lane/inv-trade-atomicity`: diff its `lots.py` (and new `trades.py`, if it lands
  first) against this task's `_apply_acquire`/`_apply_dispose`/`_undo_dispose`/`_delete_lot`/
  `resync_position_aggregates` changes and resolve as ONE composition, not two competing ones.
- The realized-event `txn_id` retrofit (decision 5) is a real, useful fix — tracked as a future,
  independently-scoped change, not silently forgotten because this ADR names it.
- Any future multi-table investment write should be checked for `locked_unit_of_work`
  composition at review time, the same standing item money's and planning's ADRs already name
  for their own tiers.

## Alternatives considered

- **Fix the vest lot-mint deadlock by having `_vest_apply` release its own locks before calling
  the top-level `apply_lot_change` choke, then re-acquire after** — rejected: `threading.Lock`
  release-then-reacquire around an external call reopens the exact TOCTOU window
  `locked_unit_of_work` exists to close (another thread could acquire the lock and mutate
  `lots`/`positions` in between), trading a deadlock for a race.
- **Give `_vest_apply` its own bespoke lot-insert SQL instead of extracting a shared `_mint_lot`
  helper** — rejected: duplicating lot-creation logic (the fx_rate/base_currency historical-rate
  stamp, the `resync_position_aggregates` follow-up) in two places is a maintenance hazard the
  extraction avoids entirely, and `_apply_acquire` now calls the SAME helper, so there is exactly
  one lot-minting code path, not two that must be kept in sync by hand.
- **Retrofit the realized-event `txn_id` link and the `_publish_committed_transaction_events`
  firing point in this SAME task, since the choke seam touches the exact code around both** —
  rejected per the owner's own money-parity ruling: this task's scope is atomicity, not new
  computed-value behavior; bundling an unrelated behavior change into an atomicity-only diff
  makes both harder to review and harder to revert independently if either turns out wrong.
- **Skip locking `txn_audit` in the composed sets, since the brief's literal table lists never
  named it** — rejected: `create_transaction`/`bulk_delete_transactions` write `txn_audit` as
  part of the SAME unit when composed via `conn=`; omitting its lock would leave that write
  racing an unlocked concurrent solo writer, the exact anti-pattern the choke seam exists to
  prevent.

## Agent provenance

- **Decided by:** Claude (implementer agent, storage plan 4b task 4 — the 9 compositions, the
  vest lot-mint deadlock guard, the `create_transaction`/`bulk_delete_transactions` `conn=`
  additions, both deliberately-unchanged items; recorded by the task 7 implementer agent, which
  independently re-verified the choke seam and the `/rewind` mechanism via the production-scale
  rehearsal cited above). Lane/branch: `lane/storage-plan1` (worktree `storage-plan1`).
- **Alternatives weighed:** a release-then-reacquire pattern for the vest deadlock was rejected
  as trading a deadlock for a race; a bespoke duplicate lot-insert was rejected in favor of one
  shared `_mint_lot` helper; bundling the realized-event `txn_id` retrofit and the publish-firing
  point into this task was rejected per the owner's money-parity scoping ruling; omitting
  `txn_audit`'s lock was rejected as reopening the exact unlocked-concurrent-write hazard the
  seam exists to close.
- **Driving artifacts:** ADR `2026-08-08-money-core-choke-points-become-one-transaction.md` (the
  `locked_unit_of_work` mechanism and lock-ordering discipline this ADR reuses verbatim); ADR
  `2026-08-08-planning-choke-points-compose-in-one-transaction.md` (the sibling-tier precedent
  for composing a service layer's own multi-table writes through the same seam); ADR
  `2026-08-09-investment-stores-on-payload-tables.md` (the schema this choke seam writes into);
  ADR `2026-07-15` (the historical-rate fx_rate/base_currency stamp `_mint_vest_lot` replicates);
  plan 4b task 4 report (`.superpowers/sdd/2026-08-08-storage-plan4b-investments/task-4-report.md`);
  this task's own rehearsal report
  (`.superpowers/sdd/2026-08-08-storage-plan4b-investments/task-7-report.md`).
- **Why this way (one line):** reusing the money core's exact atomicity primitive for
  investments' own trade/vest/option writes — rather than inventing a parallel mechanism — means
  all three tiers share one proven-deadlock-free seam and one documented discipline, and the one
  genuinely NEW risk this tier introduced (a choke calling back into another choke's own locks)
  was found by disciplined tracing and closed by extraction before it could ever hang a real vest
  apply.
