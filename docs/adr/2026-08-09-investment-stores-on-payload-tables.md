---
title: Investment stores on payload tables — the 6 book_payload_table stores, the watchlists singleton, asset_brief as a Tier-2 cache, money-in-payload-is-exact plus the finite-money import guard, no cross-table FK between investment tables, and the services/watchlist.py re-seat
status: Accepted
date: 2026-08-09
deciders: Claude (implementer agents, storage plan 4b tasks 1/2/3/5/6/7 — schema, stores, absorb, portability, rehearsal), repo owner (spec book_id ruling; the asset_brief cache-vs-Tier-1 call; D6 cutover-strategy ruling — per-store round-trip verify, rename not delete)
---

# 2026-08-09. Investment stores on payload tables

## Context

Storage plan 4b Wave B moved all 8 investment stores — `positions`, `lots`, `grants`,
`vest_events`, `option_contracts`, `realized_events`, `watchlists`, `asset_brief` — off
`JsonStore` and onto SQLAlchemy Core + Alembic (design spec
`docs/superpowers/specs/2026-08-05-storage-engine-sqlite-postgres-design.md`, D4/D5). Investments
are the load-bearing case for this migration: unlike planning (Wave A), these numbers ARE net
worth (`net_worth()`'s `market_positions`/`options_mark` bands, `unvested_base`) — a schema or
absorb mistake here silently misstates the owner's real financial position, not just a budgeting
view. Three things were already decided before a line of schema was written:

- The spec's own book_id ruling (spec:135–137): investments are Tier-1 (book-scoped, like money
  and planning), so every investment table needs a `book_id` column and a book-leading index —
  the identical shape Wave A (planning) already established and, per that wave's own ADR, built
  `book_payload_table()`/`BookPayloadTableStore` specifically anticipating investments as its
  named second consumer.
- Money's own ADR (`2026-08-08-money-core-tables-verbatim-payload-plus-promoted-columns.md`) and
  planning's own ADR (`2026-08-08-planning-stores-on-payload-tables.md`) had already answered the
  "payload vs. promoted columns" question twice, on two different data shapes, with two different
  outcomes (money: 7 promoted columns; planning: 1). Investments needed the SAME question asked
  on its own shape, not assumed to transfer.
- The owner's own ruling on `asset_brief` (a per-instrument synthesized LLM/search brief,
  distinct from the 7 real portfolio stores): it is derived, rebuildable, and not itself a fact
  the owner entered — the cache-vs-user-data axis every prior wave (`evidence`, `price_history`)
  already used to decide Tier-1 vs. Tier-2.

## Decision

We will:

1. **Six of the eight investment tables are plain `book_payload_table("<name>")`, zero
   customization** — `positions`, `lots`, `grants`, `vest_events`, `option_contracts`,
   `realized_events`. Each gets the identical `(book_id FK->books.id, seq, id PK, payload)` shape
   plus a unique `ix_<name>_book_seq` index — the same primitive Wave A built and named
   investments as the second consumer of. This is the DRY bet from Wave A paying off exactly as
   planned: zero bespoke schema or store code beyond a `tables.py` registration line and a
   `BookPayloadTableStore` subclass declaration per table.
2. **`watchlists` is a hand-rolled per-book singleton — the PK IS `book_id`**, payload the whole
   `{watchlists, items, global_config}` dict verbatim, byte-identical shape to `plan_state`
   (Wave A's own hand-rolled singleton). `WatchlistStore` hand-rolls every method over
   `_get_raw`/`_save_raw` rather than the generic flat-list surface, since its JsonStore-era
   source was never a flat list to begin with.
3. **`asset_brief` is a Tier-2 cache — `payload_table("asset_brief")`, NO `book_id`.** This is
   the one deliberate deviation from "every investment table is Tier-1": a per-instrument
   synthesized brief (LLM-drafted commentary + search context) is derived and rebuildable from
   the underlying instrument + external sources, not a fact the owner entered, keyed by
   `instrument_id` (book-agnostic — an instrument's brief doesn't change per book). It mirrors
   `instruments`'s own Tier-2 shape exactly (`seq`/`id` PK/`payload` + a unique `seq` index), is
   checkpoint-EXCLUDED (`ai.checkpoint._EXCLUDED_TABLES`), and is deliberately absent from
   `portable_bridge.BRIDGED` — not merely dormant like `evidence`/`price_history` (which stay
   wired for a future flip) but structurally unwireable: a cache keyed by `instrument_id` has
   nothing coherent to export/import independent of the instruments it describes, so there is no
   plausible future "flip to portable" for a `BRIDGED` entry to wait for. It is reached by
   `/api/reset` through one direct `wipe(conn, "asset_briefs.json")` call instead of the generic
   `BRIDGED` loop every other wiped store rides.
4. **Money-in-payload-is-exact: ZERO promoted columns this wave** — every money-bearing field on
   every one of the 8 stores (`positions.{cost_basis,quantity}`,
   `lots.{unit_cost,fees,fx_rate,original_qty,remaining_qty}`,
   `vest_events.{fmv_per_unit,price_paid_per_unit,gross_gain_native,base_amount,units}`,
   `option_contracts.{strike,premium_per_share,close_premium_per_share,collateral_amount,
   contracts}`, `realized_events.{proceeds_per_unit,disposal_fees,realized_gain,
   realized_gain_base,fx_rate,quantity}` plus its nested `consumptions[].{qty,unit_cost,
   realized_gain}`) lives inside the verbatim `payload` JSON column, exact via Python's
   shortest-round-trip float encoding — no `repr(float)`-Text promotion anywhere (unlike money's
   7 promoted columns or planning's 1, `budget_assignments.amount`). This holds because, exactly
   like planning's non-`budget_assignments` stores, no investment read queries or sums a money
   field at the SQL layer — `net_worth()`, `positions_service.hydrated()`,
   `lots_service.realized_pnl()`, `options_service.summary()` all load whole rows and compute in
   Python, precisely as they did against `JsonStore`. The rehearsal below (byte-identical parity
   hash across every net-worth-bearing figure) is the structural proof this held, not merely an
   assertion.
5. **The finite-money import guard runs PROACTIVELY, not as a post-hoc patch.** Plan 4a's own
   review found `budget_assignments` needed a finite-value guard on import (security finding
   M1) — a lesson applied here from day one:
   `backend.db.investments_io.import_payload_list` runs `money_io._assert_finite_money` on every
   net-worth-bearing field named in decision 4, above, BEFORE a row lands in `payload`. An
   `inf`/`nan`/non-numeric value (an untrusted `/api/import` bundle can carry `1e400`, which
   `json.loads` parses to `inf`) RAISES `ValidationError` and aborts the whole store's import —
   nothing partially written, matching `import_transactions`'s own all-or-nothing FR-M7
   behavior. `grants`/`watchlists`/`asset_brief` carry NO guarded fields — a grant's own
   `grant_price_per_unit` is reference-only (the money fact that feeds net worth lives on the
   vest event, already guarded, and the eventual lot/position); `watchlists`/`asset_brief` carry
   no money at all.
6. **No cross-table FK between any of the 8 investment tables.** `lot.position_id`,
   `realized_events[].consumptions[].lot_id`, `vest_events[].grant_id`,
   `position.instrument_id` — every inter-store link is an OPAQUE id, Python-filtered over
   `all()`, never a SQL JOIN. The only FK anywhere in revision 0011 is `book_id -> books.id`,
   matching money and planning exactly. `tables.py` records the ruling inline: *"NO cross-table
   FK BETWEEN investment tables... the opaque-id Python-filtered coupling... is left for the
   store layer, not enforced in SQL."*
7. **`services/watchlist.py`'s direct `JsonStore` import is REMOVED** — the last raw-file
   read/write/lock site in the investments surface is re-seated onto `WatchlistStore`'s DB-backed
   singleton. This surfaced and fixed a REAL, pre-existing bug, not merely a mechanical port: the
   migrated `read_watchlist()` → `_load()` path self-deadlocked `price_monitor.run_price_check()`
   on a fresh install's first tick — the old file-missing short-circuit never touched the store's
   lock, but the DB version reaches the seed-under-lock path while the caller already holds the
   SAME lock. Fixed with a lock-free `_get_raw()` read (3 regression tests that HUNG before the
   fix, confirmed instant after). `seed_examples.py` also gained `_seed_watchlist_db()` — the
   pre-existing seed script copied `watchlist.json` onto disk but nothing ever read it post-DB-
   cutover, so demo watchlists silently stopped seeding until this fix.
8. **The unvested-equity-excluded-from-net-worth invariant (ADR
   `2026-07-31-unvested-equity-excluded-from-net-worth`) is structurally preserved, not merely
   re-tested.** `positions.vesting_state`/`bucket` and `net_worth()`'s/
   `owned_vs_unvested_base()`'s read formulas are UNCHANGED code (confirmed by diff against the
   pre-Wave-B SHA — see the companion choke-atomicity ADR's drift check); only the storage
   substrate underneath the `positions`/`grants` reads changed. The rehearsal below re-proves the
   invariant against a REAL locked grant, not a synthetic one.
9. **Portability: 7 of 8 stores are LIVE, `asset_brief` is the one cache exclusion.**
   `positions`/`lots`/`grants`/`vest_events`/`option_contracts`/`realized_events`/`watchlist`
   were ALREADY members of `core.portable.PORTABLE_FILES` from the JsonStore era — unlike
   planning's `income_events` gap (Wave A found a real data-loss Blocker there), no equivalent
   gap ever existed for investments; this task's job was wiring the DB bridge/wipe under those
   same 7 filenames, not discovering a missing manifest entry. All 7 are undoable
   (`investments_io.import_payload_list`/`import_watchlist` fire `fire_db_write_hook`
   unconditionally, mirroring money/planning). `asset_brief` is wiped (a reset must still clear
   the cache) but never bridged/exported/imported (decision 3, above).

   | # | Store | Nature | `PORTABLE_FILES` | `BRIDGED` |
   |---|---|---|---|---|
   | 1 | `positions` | REAL — holdings | LIVE (pre-existing) | YES |
   | 2 | `lots` | REAL — cost-basis/FIFO lots | LIVE (pre-existing) | YES |
   | 3 | `grants` | REAL — equity grants | LIVE (pre-existing) | YES |
   | 4 | `vest_events` | REAL — vest history | LIVE (pre-existing) | YES |
   | 5 | `option_contracts` | REAL — derivative overlay | LIVE (pre-existing) | YES |
   | 6 | `realized_events` | REAL — disposal/P&L history | LIVE (pre-existing) | YES |
   | 7 | `watchlists` | REAL — durable monitoring config | LIVE (pre-existing) | YES (bespoke dict `Bridge`) |
   | 8 | `asset_brief` | CACHE — derived LLM/search brief | absent (correct) | absent (deliberate — see decision 3) |

**Verified at production scale (2026-08-09 dress rehearsal 4B, a scratchpad clone of the owner's
real data, live tree read-only throughout — never written to):** all 8 investment sources —
44 positions, 80 lots, 1 grant (carrying the locked-grant invariant probe, decision 8 above),
1 vest_event, 11 realized_events, 58 instruments, 7 watchlist entries, 43 cached asset briefs —
absorbed via the real `ensure_migrated()` chain 0001→0012. The live book carries zero option
contracts yet, so a 2-row synthetic option-contract fixture (one open long call, one closed short
put, both referencing a real held instrument for deterministic price resolution) was added
byte-identically to BOTH the OLD and NEW clones — disclosed here, not silently smoothed over — so
`options_service.summary()`/`realized_outcomes()` are genuinely exercised rather than trivially
vacuous. Eight investment-derived figure families —
`ledger.net_worth()`, `ledger.net_worth_history()`, `portfolio_history.portfolio_value_series()`,
`positions.hydrated()` + `portfolio_rows.projected_rows()`, `lots.lots_for_position()` +
`lots.resync_position_aggregates()` per position, `lots.realized_pnl()` +
`options.realized_outcomes()` + `options.summary()`, `positions.owned_vs_unvested_base()` (the
ADR-2026-07-31 invariant probe), and `equity.hydrated_grants()` — computed on this migrated
clone hashed **byte-identical** (sha256 `5f373e8361f7039d1425fbf328d0703fc7b70f98a6caecb9019e7ea51151a08a`,
130,752 bytes) to the SAME eight families computed by the pre-cutover (SHA `83dff62c`, ==
`cdc18f47^`, backend/ byte-identical to task 1's `2bc7e543` — verified zero code diff between the
two) service+store code running against an identically-built, still-JSON-investments copy of the
same source data — the owner's own "bet my real numbers on it" test, executed by machine, at the
rigor Plan 3's own money-core rehearsal established. `net_worth()`'s `unvested_base` and
`owned_vs_unvested_base()`'s `unvested_base` matched EXACTLY on both stacks, and `net_worth`
itself was UNCHANGED by the locked grant's presence — the invariant probe passed, not merely ran.
Full mechanism and evidence: `.superpowers/sdd/2026-08-08-storage-plan4b-investments/task-7-report.md`.

## Consequences

### Positive

- The book-scoped primitive paid for itself a second time: 6 of 8 tables needed zero bespoke
  schema or store code, confirming Wave A's own bet that naming investments as a second consumer
  up front (rather than discovering the need later) was the right call.
- Money-in-payload-is-exact means investments' schema footprint stays minimal — ZERO promoted
  columns across all 8 tables, the smallest footprint of any wave so far (money: 7, planning: 1,
  investments: 0) — because no investment read ever queries a money field relationally, and the
  rehearsal's byte-identical parity hash is the structural proof, not an assumption carried over
  from planning's own reasoning.
- The proactive finite-money guard closes the exact class of import-time bug Plan 4a's security
  review found reactively (M1) — applied here as a day-one design decision instead of a fix
  round.
- The `services/watchlist.py` re-seat closes a REAL self-deadlock bug (a fresh install's first
  price-check tick would have hung forever) before it ever reached a self-hoster.
- `asset_brief`'s cache classification means a future re-generation of every cached brief (a
  model/prompt change, say) is a pure `DELETE FROM asset_brief` + re-run — no export/import
  machinery to keep in sync with a cache that was never meant to be portable.

### Negative

- The book_id-scoped-but-unfiltered-on-read behavior (inherited from money's own `AccountStore`
  precedent, carried through planning) means every investment store, like every money and
  planning store, is not yet actually multi-tenant-safe — partition-KEYED, not
  partition-ENFORCED. Deferred identically to the money core's own deferral, not a new gap.
- Zero promoted columns means every investment money query the SQL layer might someday want
  (e.g. "sum realized gains across all positions without loading every row into Python") is
  unavailable without a future migration — accepted because no such query exists today, but a
  real cost if one is ever needed.
- The 8-store classification (decision 9's table) is a snapshot; a FUTURE investment store must
  be independently re-classified against the same cache-vs-user-data test, the same caveat
  planning's own ADR records for its 12-store table.

### Follow-ups

- If a future feature needs a relational money query over investment data, promote ONLY that
  field (mirroring `budget_assignments.amount`'s precedent), not the whole 8-table surface.
- Any future per-instrument or per-position cache (beyond `asset_brief`) should be independently
  checked against the same cache-vs-user-data axis before defaulting to Tier-1.

## Alternatives considered

- **Promote every investment money field to a typed column, mirroring money core's seven** —
  rejected: no investment read queries or sums a money field at the SQL layer (every one loads
  whole rows and computes in Python), so promotion would add schema and write-time cost for a
  query pattern that does not exist. The rehearsal's byte-identical parity hash is the empirical
  check this reasoning was correct.
- **Give `asset_brief` a `book_id` for consistency with the other 7 stores** — rejected: an
  instrument's synthesized brief does not vary per book (the same ticker means the same company
  regardless of which book holds it), so a `book_id` column would be dead weight on every row and
  would falsely imply per-book brief variation that will never exist. Mirrors `instruments`'
  own book-agnostic precedent exactly.
- **Wire `asset_brief` into `BRIDGED` "for completeness," even with no real export/import use
  case** — rejected: a `Bridge` entry requires both `export_fn`/`import_fn`; giving `asset_brief`
  one would contradict its own "cache, not user data" classification and add dead code with
  nothing to exercise it.
- **Skip the option-contract fixture entirely since the live book has none** — rejected: the
  rehearsal's whole point is proving the storage migration preserves EVERY net-worth-bearing
  figure family, and `options_service.summary()`/`realized_outcomes()` would be trivially,
  uninformatively equal (both sides return empty/zero) without at least one real exercise of the
  option code path. A disclosed, byte-identical synthetic fixture on both clones is a stronger
  proof than silently skipping the family.

## Agent provenance

- **Decided by:** Claude (implementer agents across storage plan 4b tasks 1 [schema], 2 [6 simple
  stores], 3 [asset_brief/watchlists stores + the watchlist.py re-seat], 5 [absorb + the finite-
  money guard], 6 [portability]; recorded by the task 7 implementer agent, which additionally ran
  the production-scale rehearsal cited above as independent verification of every claim in this
  ADR). Lane/branch: `lane/storage-plan1` (worktree `storage-plan1`).
- **Alternatives weighed:** full column promotion for every investment money field was rejected
  against the "nothing queries it relationally" test, verified empirically by the rehearsal's
  parity hash rather than merely argued; a `book_id` on `asset_brief` was rejected as dead weight
  on a genuinely book-agnostic cache; wiring `asset_brief` into `BRIDGED` "for completeness" was
  rejected as contradicting its own classification; skipping the option-contract fixture was
  rejected as leaving the rehearsal's options-family check uninformatively vacuous.
- **Driving artifacts:** `docs/superpowers/specs/2026-08-05-storage-engine-sqlite-postgres-design.md`
  (D4/D5, book_id spec:135–137); ADR `2026-08-08-planning-stores-on-payload-tables.md` (the
  `book_payload_table()`/`BookPayloadTableStore` primitive this ADR reuses verbatim, and the
  payload-vs-promoted precedent this ADR extends); ADR
  `2026-07-31-unvested-equity-excluded-from-net-worth.md` (the invariant the rehearsal re-proves);
  plan 4b task 1/2/3/5/6 reports and their review packages
  (`.superpowers/sdd/2026-08-08-storage-plan4b-investments/`); this task's own rehearsal report
  (`.superpowers/sdd/2026-08-08-storage-plan4b-investments/task-7-report.md`).
- **Why this way (one line):** a schema decision (promote vs. payload, Tier-1 vs. Tier-2,
  FK vs. opaque id, portable vs. cache) is only correct if it matches how investment data is
  ACTUALLY read, priced, and reset today, not how a sibling tier happened to decide it — so each
  of the nine decisions above was checked against investments' own real query/reset/restore paths,
  and the one figure that matters most (net worth) was independently re-derived byte-for-byte
  rather than trusted on architecture alone.
