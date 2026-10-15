---
title: A position's quantity is derived from its records — one writer, never edited independently
status: Accepted
date: 2026-10-07
deciders: orchestrating agent (Claude) + repo owner
---

# A position's quantity is derived from its records — one writer, never edited independently

## Context

A position stores `quantity`, `cost_basis` and `cost_currency`. The same shares are also
recorded elsewhere: in lots (owned shares), in vest events (parked two-step vests), and in
the grant's tranche schedule (unvested units). Some write paths keep the two in step. Buys,
sells and lot edits call `lots.resync_position_aggregates`, which sets qty = Σ open lots.
Other paths do arithmetic on the stored number instead:

| Writer | What it does to `position.quantity` |
|---|---|
| `positions.add_position` (REST `POST /api/positions`, AI add without `acquired_at`) | stores the caller's number; **no lot** |
| `positions.update_position` | stores the caller's qty/basis on any lot-less, non-grant position; an `account_id` change moves the **position only, never its lots** |
| `lots.resync_position_aggregates` | Σ open lots (the correct derivation) |
| `lots.ensure_opening_lot` (called by `GET /api/positions/{id}/lots`, dispose, grant mint) | creates a lot **from** the stored qty, so a read can write |
| `equity._vest_apply` / `_vest_undo` | locked qty ∓ vest units |
| `equity_release.apply_park_pending` / `unpark_pending` | pending qty ± vest units (basis is already re-derived from vest events) |
| `equity._grant_create` | locked qty = `total_units`; later tranche edits (`_grant_update`) and `_grant_cancel` **never touch it** |

Every bug in this family came from a path that kept a separate copy:

- **KSTL placeholder lot (fixed 2161c17cf).** The lots read route seeded an "opening" lot
  of 1,955 → 1,466.25 shares on an unvested grant position just because it was viewed. No
  vest ever reduced it. The same route still turns a **pending-release** position's parked
  units into an opening lot. That position is vested, has qty > 0 and has no lot, so the
  2161c17cf guard (unvested only) does not cover it. History would then count those units
  twice: once as a parked vest and once as a lot.
- **Removed position left its lots held (fixed 3259e3a03).**
- **Parked vests are missing from lot-based rebuilds (patched 182507a23 / 39c813f5c).**
- **23 lots filed under settlement wallets (root-caused here).** DATA_MODEL's custodian
  rule says positions and lots belong to the securities account (`IBKR`), and cash legs go to
  the currency wallet (`IBKR (USD)`). Snapshots of the live book show what happened. On
  5 Jun, 14 IBKR positions sat in `IBKR (USD)` together with their lots. By 15 Jun the
  positions were in `IBKR`, but 14 opening lots and 7 buy lots were still in `IBKR (USD)`.
  The Coinbase and Gemini pairs are the same pattern. One move is on record verbatim: a
  Lucius `apply_position_update` with `changes: {account_id}` and the reason "Move SHIB
  from Coinbase (USD) to Coinbase account". `update_position` still moves the position
  without its lots today. Effects: the wallet accounts cannot be deleted ("still holds N
  open lots"), and those buys show under the wrong account in activity, fees-by-account and
  `lots_acquired`. Net worth, history and returns do not change, because none of them group
  by a lot's account.

Today the live book has **zero** quantity or cost-basis drift. A dry run of the app's own
resync against a copy of the book found no difference on all 41 lot-backed positions. Every
position holding units has lots, except the grant's unvested and pending rows. So the drift
comes from the write paths that remain open, not from today's numbers.

## Decision

We will make a position's `quantity`, `cost_basis` and `cost_currency` **derived figures
with exactly one writer**: `services/position_figures.rederive(position_id, *, conn)`.
Every choke calls it inside its own unit of work, and nothing else may write those fields.
What the position is decides where the figures come from:

| Position | `quantity` | `cost_basis` / `cost_currency` |
|---|---|---|
| a grant's **locked** (unvested) position | Σ units of the grant's tranches that are not vested; **0 once the grant is cancelled** (forfeited) | none (unvested units have no basis) |
| a grant's **pending-release** position | Σ units of its vest events with `pending_release` | `equity_release.pending_basis` (unchanged) |
| any other position (owned and delivered) | Σ `remaining_qty` of its open lots | the current `resync_position_aggregates` rule (unchanged) |

The figures stay **materialized** on the row, so all ~40 readers (`hydrated()`, net worth,
monitoring, AI tools) read exactly what they read today. They are never independently
**writable**. Concretely:

1. **`PositionStore.touch` refuses `quantity` / `cost_basis` / `cost_currency`.**
   `PositionStore.create` stamps quantity 0 and no basis. A dedicated
   `PositionStore.write_figures(...)` is the only store entry for those fields, and a repo
   guard test pins `services/position_figures.py` as its only caller. `resync_position_aggregates`
   becomes the owned-position branch of `rederive`. The vest, undo, park, unpark, release,
   grant create/update/cancel and lot paths stop doing ± arithmetic and call `rederive`.
2. **Every holding has a record.**
   - `apply_position_change(add)` books the position's lot straight after creating the
     shell: a `buy` lot when an `acquired_at` is given (as today), otherwise an `opening` lot
     dated **today** (decision 1 below). If the lot is refused, the shell is deleted, so no
     empty holding is left behind.
   - REST `POST /api/positions` goes through the same choke instead of calling
     `add_position` directly.
   - A position with `vesting_state="unvested"` is created only by the equity choke,
     because unvested units come from a grant's schedule. A hand-made unvested position
     has no record to derive from.
3. **A manual quantity or basis edit is refused with guidance.** It names the record to
   write instead: a buy or transfer in (a new lot), a sale or transfer out (a disposal), a
   lot fix (edit it in the lots list, or delete it and record it again), or the grant/vest
   flow. `vesting_state` is likewise grant-only. Lucius's `update_positions` preview refuses
   before staging a confirm card, so the user is never asked to approve an edit that would fail.
4. **An account move carries the position's lots.** `update_position(account_id)` sets every
   lot's `account_id` (open and closed) in the same unit of work. A lot's `account_id` stays
   a stored copy rather than a join, because fully-sold lots outlive their deleted position
   (`remove_position` keeps them as history) and then the copy is the only record of the venue.
5. **The lots read route is a pure read.** `GET /api/positions/{id}/lots` stops calling
   `ensure_opening_lot`. After the migration below, every owned position that holds units
   already has a lot, so `ensure_opening_lot` and `projected_open_lots`' synthetic branch are
   deleted, not guarded.
6. **One-time Alembic migration** (`202610111200_every_holding_has_a_record`) does three things:
   - (a) inserts ONE `opening` lot for every remaining lot-less owned position with qty > 0.
     It never touches grant-managed or unvested positions, and uses the deterministic
     `_opening_lot_id`, so a re-run is a no-op.
   - (b) re-files every lot whose `account_id` differs from its live position's onto the
     position's account.
   - (c) runs a **parity check** of every owned position's stored quantity against
     Σ its open lots, and **logs** any difference. It writes no position row.
     *Changed from the plan:* the plan said "raise". A raise fails the boot migration, which
     would lock a self-hosted user out of their own app over a figure nothing else can fix.
     So the correction happens in (7) instead, visibly. On the live book: (a) mints 0 lots,
     (b) re-files 23, and (c) logs nothing.
7. **Parity is checked at boot and in tests.** `position_figures.reconcile()` runs at boot,
   after the lot repairs. It re-derives any position whose stored figures disagree with its
   records and posts a notification naming each holding and its old → new quantity. The
   boot's pre-migrate backup keeps the before-image. Drift can now enter only through a raw
   write or a restored older backup. `check_parity()` is also asserted after every step of a
   property test that runs random sequences of writes (add, acquire, dispose, undo, lot
   delete/amend, move, remove, vest, release, vest undo, grant cancel) over random books.
8. **Returns price an opening lot at market, not at cost.** An `opening` lot (held when
   tracking started) enters the period's cash flows at its **market value on its date**,
   not at `qty × unit_cost`. No money moved in the tracked period, and pricing it at an old
   cost would count the whole gain since purchase as this period's return (the KSTL +9.6%
   class of bug). A lot whose date the user corrects to the real purchase day prices at
   about its cost anyway, so the rule needs no marker for "the app picked this date", and
   nothing switches the lot's kind behind the user's back. It falls back to cost when no
   price exists. Undated legacy opening lots fall in no period and are unaffected. An
   opening lot booked WITH a funding transaction (real cash out) is a purchase, and keeps
   its cost.
9. **Grant edits keep the records honest** (from the review, 2026-10-08):
   - A tranche edit takes each tranche's vest state from the STORE, never from the payload.
     A vested tranche cannot be resized or dropped until its vest is undone. Otherwise an
     edit without the flags re-counted vested units as unvested (800 shares for a 600-unit
     grant), and an edit that set them dropped units with no vest behind them.
   - A grant adopts (`locked_position_id`) only an unvested row with no lots and no other
     grant. Adopting an owned, lot-backed holding would overwrite its quantity and strand
     its lots.
10. **The one row with no record.** An unvested position no grant owns could be made by
    hand before this change; both paths are refused now. Its stored quantity is its only
    record, so `derive` returns nothing for it: it is never zeroed and never edited, and
    parity skips it.
11. **A grant's locked and pending-release holdings hold no lots** (amendment, 2026-10-11).
    Their figures come from the grant and its vests, so a lot there moved no number and
    parity never saw it — but it was sellable. The old lots read wrote exactly that: viewing
    a pending-release holding's lots seeded an `opening` lot of its parked units (488.75
    KSTL on the owner's book, found while snapshotting it before upgrading). Revision
    `202610111300_grant_holdings_hold_no_lots` removes that shape only — `opening`, no
    linked transaction, never sold from — and logs each removal; boot reports any other
    lot on such a holding (`position_figures.stray_grant_lots`) and leaves it for a person.

## Consequences

### Positive
- Two copies can no longer disagree. Every path that could write a position's number now
  either produces it from records or is refused.
- The pending-release placeholder hazard, the stale-after-cancel unvested count, stale
  unvested units after a tranche edit, and wallet-filed lots all close at their root.
- Readers do not change, so no figure the user sees moves on the live book. The only
  changes are the 23 lots re-filed to their position's account, with no change in value.

### Negative
- A position whose quantity is "just a number" no longer exists. Adding one without a
  purchase date now writes a lot, and a manual correction must be phrased as a buy, sale,
  transfer or lot amend. This is more friction for a quick "I actually hold 101" fix.
- `rederive` for a locked or pending position reads `grants` / `vest_events`, so
  `positions`-only lock sets grow to include those tables where they derive grant rows.
- The materialized copy still exists. The invariant is enforced by a single writer, a store
  guard and a parity test, not by the schema. A raw SQL write or a restore of an old backup
  can still plant drift. The boot parity log is the backstop.

### Follow-ups
- `docs/INVARIANTS.md` (Investments) and `docs/DATA_MODEL.md` (Positions, Tax lots, Equity
  comp) state the rule. Done with this ADR.
- The AI tool descriptions for `update_positions` / `add_positions` / `cancel_grant` changed
  their wording. (`test_tool_schema_budget` was already failing on main before this change.)
- ~~Lucius has no lot-amend tool~~ — done 2026-10-08: `update_lots` (`ai/tools/lot_updates.py`)
  corrects a lot's date/quantity/cost/fees/kind/note through `apply_lot_change(amend)`, refused
  for a vest's lot, validated on every path (unattended included). A sold lot became correctable
  2026-10-13 (ADR `2026-10-13-a-sold-lot-is-correctable-and-its-sales-keep-their-allocation`).
- `lot_integrity.repair_unvested_placeholder_lots` becomes unreachable once the read route is
  pure. Keep it one release for books restored from older backups, then remove it.
- Out of scope, noted: a grant's account move relocates the locked position but not its
  pending-release position.

## Owner decisions (2026-10-08)

1. **An add with no purchase date** becomes an `opening` lot **dated today**: tracking starts
   here, like an account's Starting Balance. Past history is not rewritten. Returns use the
   market value that day (decision 8). The user sets the real date on the lot when known,
   from the lot editor; history, returns and holding period then follow it. Rejected: an
   undated lot (rewrites past history as if held since inception) and requiring a date
   (friction on statement imports that list holdings without dates).
2. **A cancelled grant's unvested units count as 0.** Cancel means forfeited; until now they
   kept counting in `unvested_base`.
3. **A manual quantity edit is refused with guidance**, not auto-booked as an adjustment lot.
   An upward adjustment is already an acquire. A downward one has no honest form: a disposal
   books a gain or loss, and a lot cannot be negative.

## Alternatives considered

- **Compute quantity on every read (drop the stored field).** Rejected. About 40 readers,
  including raw-store reads in `monitoring/` and mentions, would each need rerouting, and
  `hydrated()` would join lots, grants and vest events on every call. A single writer plus a
  parity check enforces the same invariant without changing any reader.
- **Keep the per-path guards (status quo plus more patches).** Rejected. Each of the four
  bugs above was a guard added after the fact to one path. The pending-release lot-read
  hazard shows the next one is already reachable.
- **Unvested units as lots (a `grant` lot per tranche, flagged unvested).** Rejected. Lots
  mean "acquired". Every lot-based reader (history, returns, P&L, disposal queue) would need
  an unvested filter, which is the exact filter whose absence caused the KSTL bug.
- **Auto-mint an adjustment lot on a quantity edit.** Rejected for now. See open question 3.
- **Derive a lot's `account_id` from its position (drop the field).** Rejected. Fully-sold
  lots of a removed position keep history, and with no live position to join to, the stored
  copy is the only record of the venue.
- **Lazy opening lots (today's `ensure_opening_lot`).** Rejected. It makes a read a write,
  and it built the KSTL placeholder.

## Agent provenance

- **Decided by:** orchestrating Claude agent (Opus 5.5), pending owner ratification
- **Lane / branch:** `derived-position-quantity`, worktree `onw-derived-qty`
- **Alternatives weighed:** on-read derivation vs a single writer with a materialized copy; per-path guards; unvested lots; adjustment lots; a derived lot account
- **Driving artifacts:** commits 2161c17cf, 3259e3a03, 182507a23, 39c813f5c; live-book investigation 2026-10-07 (read-only parity: 0 drift; 23 wallet-filed lots traced to a position-only account move via June snapshots and a recorded `apply_position_update`)
- **Why this way (one line):** a number with two writers drifts, so give it one writer that reads the records, and refuse every other path.
