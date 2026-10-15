---
title: A `positions` table write notifies watchlist reconciliation — new core.holdings_change seam
status: Accepted
date: 2026-08-23
deciders: owner (bug report), implementing Claude agent
---

# A `positions` table write notifies watchlist reconciliation

## Context

Earlier the same day (commit `69852e7c`), `get_watchlist` stopped reconciling the auto-managed
"My holdings" watchlist on every read — a `get_` tool writing a data domain on every call was
itself the bug. Reconciliation moved to `scheduler._run_watchlist_sync` (every 24h, plus an 8s
post-boot kick-start) and stayed inline on the two REST endpoints that already synced when the
Watchlist screen opens.

That fix's own commit message named the gap it left open: nothing reconciles when holdings
**actually change**. A position added or removed via chat, a trade, a broker import, or an
equity vest sits stale in "My holdings" until the daily job fires or someone happens to open the
Watchlist screen — the exact "worse than before" case the original commit message called out and
deliberately deferred.

Positions change through more than one write choke: `positions_service.apply_position_change`
(add/update/remove), `lots_service` acquire/dispose/undo (via `resync_position_aggregates`), and
the equity vest apply/undo choke. Hanging a reconcile call off each one individually is exactly
the "remember to add it at every new site" failure mode the owner explicitly wants avoided.

Tracing all of them: every one of those paths' actual quantity write lands through
`PositionStore.create` / `.touch` / `.delete` (`backend/db/position_store.py`) — that is the one
true convergence point, at the `db/` layer.

Two mechanisms were considered and rejected before landing on the one below:

1. **`core.io.fire_db_write_hook`** — already fired unconditionally by every `PayloadTableStore`
   write, including `positions`. Rejected: it is a single-slot hook already claimed by
   `ai.checkpoint` (last registration wins — a second registrant would silently replace the
   checkpoint hook), and, more fundamentally, it fires **before** the write lands (a pre-write
   before-image hook for `write_log`), not after — reconciling from it would read stale
   (pre-write) position state.
2. **A reconcile call inside each service-layer choke** — rejected per the "remember every site"
   problem above; also, `lots_service`/`equity` compose their `PositionStore.touch` call inside
   their own already-open `locked_unit_of_work` transaction, so a reconcile call placed at that
   level would either need to also know about `conn=` composition (duplicating logic three
   times) or would accidentally run the watchlist write inside the trade choke's own money
   transaction — the thing this decision specifically avoids (see below).

## Decision

**A new core-layer seam, `backend/core/holdings_change.py`** — `register_holdings_changed_hook(fn)`
/ `notify_holdings_changed()`, a single-slot registrant, best-effort, logged, mirroring
`core.automation_runner`'s register/fire shape (a dependency inversion so `db/` can notify a
`services/`-layer consumer without importing upward — CLAUDE.md's dependency rule:
`api → ai → services → db → core`).

`PositionStore.create` / `.touch` / `.delete` (`backend/db/position_store.py`) call it after a
REAL write (a no-op `touch`/`delete` — id not found — fires nothing), through a small helper,
`_notify_holdings_changed(conn)`, that picks the right commit boundary:

- **`conn=None`** (every call except the two below): the mutate's own `_use_conn` already opened
  and committed its own independent transaction by the time control returns, so firing
  immediately is correct.
- **`conn` given** (`lots_service.resync_position_aggregates` after an acquire/dispose/undo; the
  equity vest apply/undo choke): the write is composed inside a caller's still-open
  `locked_unit_of_work`. Firing immediately here would run the watchlist read+write **inside**
  that money choke's own transaction — extending a trade/vest transaction across an unrelated
  domain, which CLAUDE.md rules out — and would stall against SQLite's single-writer slot until
  `PRAGMA busy_timeout` (5s) before raising "database is locked", since `WatchlistStore` would
  try to open a second write transaction on the same engine while the first is still open.
  Instead this defers via **`db.engine.on_commit`**, the exact seam `services/ledger.py` already
  uses for the identical class of problem (a composed choke's post-write side effect that needs
  its own transaction) — it fires exactly once, strictly after the outer transaction actually
  commits, and not at all on a rollback.

`backend/services/watchlist.py::reconcile_from_holdings_change()` is the registered handler — the
same algorithm the scheduler job and the two REST endpoints already run
(`positions_service.hydrated()` → `WatchlistStore().sync_from_holdings(...)`), just a third
trigger, not a new reconciliation rule. It is registered as a load-time side effect of importing
`services.watchlist` — the same mechanism `ai.checkpoint` uses to register `core.io`'s hooks —
which `backend.main → api.watchlist → services.watchlist` guarantees happens at process boot,
before any position write can occur.

`get_watchlist` stays a pure read (`tests/test_tool_domains.py::
test_get_watchlist_is_a_read_and_a_job_owns_the_reconciliation` is unaffected — the scheduler job
and its registration are untouched; this decision adds a third, independent trigger rather than
replacing the job).

## What did NOT change

- `get_watchlist` — still a pure read, no `mutates=` tag, still in `READ_ONLY_TOOLS`.
- `scheduler._run_watchlist_sync` — unchanged code, now documented as the BACKSTOP for whatever
  the new trigger doesn't reach (a server-down window, or any future write path that bypasses
  `PositionStore`).
- The two REST endpoints' inline sync — unchanged.
- `WatchlistStore.sync_from_holdings`'s own reconcile algorithm (add owned, prune non-owned) —
  unchanged; this decision only adds a caller.
- No money write choke's transaction now spans more than the tables it always composed
  (`locked_unit_of_work`'s table list, per choke, is untouched) — the watchlist write always runs
  in its own, separate transaction, opened after the money choke's has already committed.

## Consequences

### Positive
- A position add/remove/quantity-change from any of the three real write paths (direct edit, lot
  acquire/dispose, equity vest) is reflected in "My holdings" immediately, not up to 24h later.
- One catch-all (`PositionStore`'s three write methods) instead of three call sites to remember —
  a future new write path only needs this if it bypasses `PositionStore` entirely, which nothing
  legitimate does today.
- Reuses an existing, already-proven pattern (`db.engine.on_commit`, exercised by
  `services/ledger.py` and covered by `tests/services/test_trade_choke_atomicity.py`'s
  self-deadlock regression test) rather than inventing a new commit-boundary mechanism.

### Negative
- `PositionStore`, a `db/`-layer store, now has a behavioral side effect beyond persistence
  (still layering-clean via the inversion seam, but a reader of `position_store.py` in isolation
  needs to follow into `core.holdings_change` to see the full picture).
- Every position write now pays the cost of `positions_service.hydrated()` (instrument/account/
  price-history/grant store reads, no live price fetch) plus a `WatchlistStore` read-modify-write,
  even when the change doesn't affect ticker membership (e.g. an account_id-only edit). Accepted
  per the owner's own framing of the gap ("cheap and idempotent... not free") — no live network
  call, and `sync_from_holdings` no-ops (no store write) when nothing actually differs.
- A future new position-quantity write path that does NOT go through `PositionStore.create`/
  `.touch`/`.delete` (a raw SQL write, or a new store bypassing this one) silently loses this
  trigger — mitigated, not eliminated, by the scheduler job's daily backstop.

### Follow-ups
- None required by this change.

## Alternatives considered

- **`core.io.fire_db_write_hook`** — rejected: single-slot (already claimed by the checkpoint
  hook) and pre-write, not post-commit. See Context.
- **A reconcile call added to each of `apply_position_change`/`resync_position_aggregates`/the
  equity vest choke individually** — rejected: the "remember every new site" trap, and a
  services-layer call site would still need its own composed/standalone branching duplicated
  three times instead of once at the true convergence point.
- **Firing the watchlist sync from inside the SAME `locked_unit_of_work` transaction as the
  triggering trade/vest** — rejected outright: extends a money choke's transaction across an
  unrelated domain (explicitly ruled out by CLAUDE.md) and self-stalls against SQLite's
  single-writer slot (empirically the same failure class `db.engine.on_commit`'s own module note
  documents and `test_vest_apply_does_not_self_deadlock_on_lot_mint` guards against for a
  structurally identical case).
- **A generic "fire on any table write" hook on `PayloadTableStore` itself** — rejected as
  broader than needed: only the `positions` table matters for this reconciliation, and adding an
  unconditional per-write hook to the base class used by ~40 stores is a much larger surface for
  a problem this specific to one table.

## Agent provenance

- **Decided by:** owner (bug report — "nothing reconciles when holdings actually change");
  implementing Claude agent chose the mechanism.
- **Lane / branch:** `main` (direct, small self-contained backend change).
- **Alternatives weighed:** `core.io.fire_db_write_hook` vs. a new dedicated seam; per-call-site
  reconcile vs. one catch-all at `PositionStore`; immediate-fire vs. `on_commit`-deferred for the
  composed case — see "Alternatives considered".
- **Driving artifacts:** commit `69852e7c` ("a scheduled job reconciles My-holdings, not a
  read") and its own stated gap; `backend/db/engine.py`'s `on_commit` module note;
  `tests/services/test_trade_choke_atomicity.py::test_vest_apply_does_not_self_deadlock_on_lot_mint`
  as the precedent for the failure mode being avoided.
- **Why this way (one line):** every position-quantity write already funnels through
  `PositionStore`, so notifying from there — deferred via the existing `on_commit` seam when
  composed — closes the gap with one call site and no new transaction risk.
