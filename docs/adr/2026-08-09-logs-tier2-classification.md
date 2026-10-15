---
title: Logs/telemetry stores on payload tables — Tier and checkpoint/portability are independent axes; automation_runs is the one Tier-2 table that stays checkpoint-included
status: Accepted
date: 2026-08-09
deciders: repo owner (the automation_runs checkpoint-inclusion call), Claude (implementer agent, storage plan 4c tasks 1/2/3/4 — schema, absorb, portability, rehearsal)
---

# 2026-08-09. Logs/telemetry stores: Tier-2 classification, and why `automation_runs` is the exception

## Context

Storage plan 4c (Wave C) moves the 9 remaining logs/telemetry `JsonStore`s —
`automation_run_store`, `automation_runtime_store`, `batch_ledger_store`,
`harness_suggestion_store`, `mcp_write_log_store`, `model_telemetry_store`,
`pending_analysis_store`, `run_history_store`, `tool_call_log_store` — onto
`backend.db.payload_store.PayloadTableStore`, the same primitive Plans 2-4b already proved
(design spec `docs/superpowers/specs/2026-08-05-storage-engine-sqlite-postgres-design.md:196-197`
names this tier's append-only-log membership explicitly).

Every prior wave's classification question collapsed onto ONE axis: Wave A (planning) and
Wave B (investments) both found "does spec:135-139 call this book-scoped user data" answered
book_id, Tier, AND checkpoint/portability treatment together — Tier-1 meant real user data
meant book-scoped meant checkpoint-included meant portable, all at once. This wave's own
recon (task 1 brief, Design Note 1) found that framing does not hold here:
`automation_run_store` is Tier-2 (no `book_id` — an automation run isn't owned by a
particular book any more than `automations.json` itself is) **but** is checkpoint-INCLUDED
and already sits in `core.portable.PORTABLE_FILES` today (`AUTOMATION_RUNS_JSON`'s own
comment: "durable run history — portable"; confirmed absent from
`ai.checkpoint._EXCLUDED_PATHS`). Its 8 siblings are Tier-2 **and** checkpoint-EXCLUDED **and**
absent from `PORTABLE_FILES`. Getting this wrong in either direction has a real cost: treating
`automation_runs` like its 8 siblings would silently strip a user-visible run history out of
every `/rewind` and every data export; treating any of the 8 siblings like `automation_runs`
would let a `/rewind` resurrect a stale scheduler queue, replay an already-delivered event, or
rewrite the record of what an MCP write actually did.

## Decision

**Tier (the `book_id` question) and checkpoint/portability (the "does a restore/export touch
this" question) are independent axes**, decided separately per store, not inferred from one
another:

1. **All 9 tables are plain `payload_table(name)`** — `(seq, id, payload)`, NO `book_id`, no
   cross-table FK. None is book-scoped user data: automation execution records, scheduler
   queues/outboxes, provider batch-job tickets, a self-harness suggestion log, an MCP audit
   ring, a model-latency ring, a crash/news analysis queue, an agent/task run ledger, a
   tool-call telemetry ring. `automation_run_store` — the one table that reads as closest to
   "real user data" on other axes (a human-visible run history, not pure telemetry) — still
   carries no `book_id`: an Automation itself isn't book-scoped, so neither is its run log.
2. **`automation_runs` is checkpoint-INCLUDED; its 8 siblings join
   `ai.checkpoint._EXCLUDED_TABLES`.** A run history is user-visible state the owner reasonably
   expects a `/rewind` to cover (an Automation that ran, then got undone by a rewind, should
   show that in its history too) and was already portable — behavior-preserved, not a new
   decision. The 8 siblings are runtime/behavioral state whose reversion or export would do
   active harm: reverting `automation_runtime`/`batch_ledger`/`pending_analysis` would replay
   or lose in-flight scheduler work; reverting `mcp_write_log`/`tool_call_log` would rewrite
   the forensic record of what already happened (both are written *inside* the very
   `CheckpointContext` they'd otherwise be captured by, mirroring `txn_audit`'s own exclusion
   rationale); reverting `model_telemetry` would (per the 2026-07-11 regression this exact
   exclusion already fixed once, at the file level) mint a checkpoint on every plain Q&A turn;
   reverting `run_history`/`harness_suggestions` would erase a failure record or resurrect a
   dismissed suggestion the user already acted on. Each new `_EXCLUDED_TABLES` entry mirrors
   its existing file-level `_EXCLUDED_PATHS` sibling (kept there too, unmodified — the
   `corrections`/`asset_brief` precedent from Waves A/B), so the file-level and table-level
   answers can never silently drift for the same store. The per-store classification (task 3's
   own binding table, reproduced here so this ADR is the one place a future reader finds the
   full picture without cross-referencing a task brief):

   | Store / table | `_EXCLUDED_TABLES`? | `BRIDGED`/`PORTABLE_FILES`/`wipe_all`? | Why |
   |---|---|---|---|
   | `automation_runs` | NO — stays included | YES | Real, user-visible run history; already portable pre-wave |
   | `automation_runtime` | YES | NO | Scheduler queue/outbox/ownership — replaying an older snapshot replays or loses events |
   | `batch_ledger` | YES | NO | Pending/completed provider batch jobs — re-fetchable from the provider |
   | `harness_suggestions` | YES | NO | Runtime/behavioral proposal log |
   | `mcp_write_log` | YES | NO | Observability audit ring, not a gate; written inside the per-call `CheckpointContext` |
   | `model_telemetry` | YES | NO | Runtime measurement, written on EVERY model turn (2026-07-11 regression precedent) |
   | `pending_analysis` | YES | NO | Runtime scheduler drain/fill queue |
   | `run_history` | YES | NO | A restore must never erase a failure record the user is relying on |
   | `tool_call_log` | YES | NO | Written inside every turn's `CheckpointContext`; a rollback must never rewrite what was called |

3. **No cross-store FK; the one soft inter-store reference stays Python-level, opaque, and
   unenforced.** `automation_runtime_store.claim_run`/`run_owners`/`reconcile_event_batches`
   read/write a `running_run_id` value that IS an `automation_run_store` row id — the identical
   "opaque id, no DB JOIN" looseness Waves A/B already established for
   `envelopes.group_id`/`lot.position_id`/etc.
4. **Money-shaped fields stay raw payload, unguarded.** `cost_usd`
   (`automation_runs`/`run_history`) and `base_amount` (`mcp_write_log`) are AI-cost/audit
   telemetry — never summed into net worth or any financial total — so unlike investments'
   proactive finite-money guard (money that DOES feed net worth), these fields get no
   `_assert_finite_money` call.
5. **`fail_closed_on_corrupt=False` has no DB-era equivalent — dropped, not ported.** Five of
   the nine JsonStore classes set this today ("a corrupt telemetry file must never raise into
   a tool call"). File corruption is a file concept; a DB read either succeeds or the database
   itself is broken, which is `db.migrate.ensure_migrated()`'s fail-closed boot gate's job,
   orthogonal to any one store — there is nothing to opt out of at this layer. The JsonStore-era
   corrupt-file tests these five stores had are retired with a comment, not replaced with an
   invented "tolerate a corrupt DB row" mode.
6. **Documented gotcha for the next agent who touches one of these tables: `PayloadTableStore
   .all()` is ASCENDING, and the 4 prepend-style stores (`harness_suggestion_store`,
   `mcp_write_log_store`, `tool_call_log_store`, `run_history_store`) all promise
   newest-first.** Their JsonStore-era shape got that ordering for free (a Python list prepend
   on every append); the generic base class's `all()`/`query()` do not reproduce it, so each of
   the four overrides every method that promises newest-first with an explicit `ORDER BY
   seq DESC` (`recent`/`all_rows` on `tool_call_log_store`, the equivalent on the other three).
   Reaching for the inherited `all()` on any of these four silently returns oldest-first — a
   correctness bug with no type error to catch it, since both are `list[dict]`.
7. **`automation_runtime_store._mutate_state`'s port is the reusable pattern for any future
   keyed-singleton-per-id store that needs a closure-shaped compare-and-set engine**, distinct
   from the two direct CAS primitives above (`AutomationRunStore.patch`/`.transition`,
   `PendingAnalysisStore.claim`/`.mark_failed`/`.upsert_pending`). Its ~10 domain methods
   (`clear_fields`/`update_event_state`/`ensure_trigger_revision`/`enqueue_events`/
   `claim_event_batch`/`complete_event_batch`/`restore_event_batch`/`claim_run_with_result`/
   `release_run`/`stage_event_delivery`/`acknowledge_event_delivery`) are byte-identical pure
   `state -> (changed, result)` closures, ported verbatim from the JsonStore era — the SHAPE
   of "read the current per-id state, compute a decision and a new state, write it back
   atomically" survives a storage-engine swap untouched. Only `_mutate_state`'s own body (the
   PERSISTENCE engine each closure runs inside) was rewritten, from a per-file lock plus
   whole-list `_load_items`/`_save_items` read-modify-write to `_use_conn`/`_row`/`_fire_hook`
   under the table's own lock — same atomicity guarantee (read-decide-write, one transaction),
   different substrate underneath. A future store with the same "one row per key, CAS-mutated
   by an arbitrary caller-supplied decision function" shape should port its engine the same
   way: keep the closures, replace only what backs them.

This task (schema revision `0013` + all 9 store rewrites) makes the classification real in
code: `tables.py`'s 9 `payload_table()` calls, the checkpoint-exclusion split above, and every
store subclassing `PayloadTableStore` with byte-identical public surfaces. No data absorption
happens yet — every JSON source file is untouched, renamed only by a later revision (`0014`),
which is also when `automation_runs`' actual portability bridge/wipe wiring and this wave's
production-scale rehearsal land (mirroring the investments/planning ADRs' own multi-task
provenance — this document will gain that evidence once those tasks close).

Task 2 (revision `0014`) absorbed all 9 JSON sources with an exact round-trip verify per
store, aborting the whole migration on any mismatch (mirrors `0012`'s investments-cutover
shape). Task 3 wired `automation_runs` into `portable_bridge.BRIDGED`/`_WIPE_FNS` (the one
logs store with a reset/export role) and left its 8 siblings out of both entirely — not even
wipe-only like `asset_brief` — since none of the 8 had a reset role to preserve. Task 4 ran
the rehearsal below.

**Verified at production scale (2026-08-09 rehearsal, plan 4c task 4, a scratchpad clone of
a representative book — the live tree read-only throughout, never written to; NO net-worth
parity, per this wave's own binding — these 9 tables feed no financial figure).**
**Round-trip smoke:** the full chain `0001`→`0014` absorbed the representative book in 5.311s; 8 of the
9 sources were present (`harness_suggestions.json` does not exist on the live tree yet — zero
suggestions generated so far — the identical "genuinely absent, not a bug" shape
`option_contracts.json` had in Wave B's own rehearsal); every PRESENT source's post-migration
DB export matched its pre-migration canonical-JSON sha256 EXACTLY, every one was renamed
`<name>.json.migrated-<ts>`, and every one's `json_cutovers` row confirmed (`source_hash`
match, `renamed=1`) — independently re-verified by this rehearsal's own hashing, not merely
inferred from the migration completing without raising. **Reset-classification proof, live,
not just unit-tested:** seeded one synthetic row into each of the 9 tables on top of the real
absorbed data, then called the real `/api/reset` handler in-process — `automation_runs`
emptied (222→0 rows) AND was captured into the pre-wipe timestamped backup (row count
verified equal to its pre-reset count); the other 8 tables' row counts were BIT-FOR-BIT
unchanged by that same reset call — the Task 3 classification proven end to end on a
realistic-scale book, not only by the per-table unit tests. **`/rewind` proof, both
directions, one turn:** `AutomationRunStore.start()` then `.transition()` edited a real row
while `ToolCallLogStore.append_many()` appended a new row to the excluded ring in the SAME
turn; the finalised checkpoint's own `db_tables` recorded ONLY `automation_runs`
(`tool_call_log` absent); `restore(mode="data")` reverted the `automation_runs` row
byte-exact (sha256 match against its pre-turn capture) while `tool_call_log`'s full row-set
sha256 — grown by the turn — was IDENTICAL before and after the restore, proving the
exclusion holds under a real restore rather than only by `_EXCLUDED_TABLES` membership. Full
evidence (counts/hashes per table, the reset backup detail, the full rewind hash table):
`.superpowers/sdd/2026-08-08-storage-plan4c-logs/task-4-report.md`.

## Consequences

### Positive

- The independent-axes framing is reusable: a future Tier-2 store that is nonetheless
  user-visible (or a future Tier-1 store that is nonetheless a derived cache, as `asset_brief`
  already is) has a named precedent instead of forcing a Tier decision to imply a checkpoint
  decision it doesn't actually determine.
- Preserving `automation_runs`'s existing checkpoint-inclusion and portability behavior
  exactly (rather than "normalizing" it to match its 8 siblings for consistency) avoids a
  silent regression a purely mechanical migration would have been tempted to introduce.
- Zero new schema primitives, zero cross-table FKs, zero promoted money columns — the smallest
  footprint of any wave so far, because none of the 9 stores' data is ever queried or summed
  relationally.

### Negative

- The two-axis framing is one more thing a future store's classification has to get right
  explicitly rather than inheriting for free from its Tier — the checklist grows by one
  question per store, forever.
- `automation_runs` alone carrying checkpoint-inclusion among 9 otherwise-uniform tables is an
  asymmetry a future reader might reasonably (but wrongly) assume is an oversight rather than
  a deliberate, behavior-preserving choice — this ADR is the answer when that question comes
  up again.

### Follow-ups

- Task 2 (revision `0014`) absorbed the 9 JSON sources into these tables with a per-store
  round-trip verify; task 3 wired `automation_runs`' bridge/wipe into `core.portable`/
  `/api/reset`; task 4 ran the production-scale rehearsal and extended this ADR with its
  evidence, matching the investments/planning ADRs' own shape (see "Verified at production
  scale" above). All three landed as planned — closed, not open.
- **Genuinely still open, NOT decided by this wave**: whether "reset means reset" should
  extend to the 8 excluded logs the way it already does for `txn_audit`/`merge_journal`
  (Plan 3) — i.e. should `POST /api/reset` also wipe `automation_runtime`/`batch_ledger`/
  `harness_suggestions`/`mcp_write_log`/`model_telemetry`/`pending_analysis`/`run_history`/
  `tool_call_log`, or is a reset legitimately scoped to user-facing data only, leaving
  operational/runtime logs to accumulate across a reset? This wave deliberately preserved
  the pre-existing behavior (none of the 8 were reachable by `wipe_all` before this wave;
  none are after it either) rather than deciding the question as a side effect of a storage
  port. Flagged here for the owner; not blocking, since nothing regresses by leaving it
  open — the worst case today is a self-hoster's `/api/reset` leaving 8 operational logs
  intact, exactly the status quo before this wave touched anything.

## Alternatives considered

- **Infer checkpoint/portability from Tier, as prior waves did** — rejected: this is the
  literal alternative the recon in Design Note 1 found does NOT hold for `automation_runs`;
  forcing it would have either wrongly excluded a user-visible run history from `/rewind` and
  export, or wrongly included 8 runtime logs a restore must never touch.
- **Give every one of the 9 stores a `book_id` "for consistency" with Tier-1 waves** —
  rejected: none of the 9 is book-scoped user data even by the loosest reading (an Automation,
  a batch job, a tool call are not owned by a particular book), so a `book_id` column would be
  dead weight on every row of every table.
- **Invent a DB-native "tolerate corrupt data" mode to replace the retired
  `fail_closed_on_corrupt=False` opt-out** — rejected: there is no DB-analogue failure mode to
  tolerate (a JSON-parse-corrupt file has no equivalent at the SQL row level); the boot-time
  fail-closed migration gate already owns "the database itself is broken."

## Agent provenance

- **Decided by:** repo owner (the automation_runs checkpoint-inclusion/portability-preserve
  call, and the "8 siblings are excluded" split — recorded as "controller-accepted" in the
  task 1 brief); Claude (implementer agent, storage plan 4c tasks 1/2/3/4 — schema, absorb,
  portability, rehearsal). Task 1 additionally found and fixed 3 production bugs while
  materializing this decision (see task 1 report) — `PendingAnalysisStore(PENDING_ANALYSIS_JSON)`
  called positionally from 3 monitoring modules (a brief-stated "no non-test caller passes it"
  claim that was empirically false), and a cross-process file lock in
  `services/automations/events.py` that derived its path from the now-removed
  `AUTOMATION_RUNTIME_JSON` constant. Task 4 ran the production-scale rehearsal (see "Verified
  at production scale" above) that independently re-proves the round-trip, the reset
  classification, and the checkpoint-exclusion split live, on a representative book, rather
  than by unit test alone. Lane/branch: `lane/storage-plan1` (worktree `storage-plan1`).
- **Alternatives weighed:** inferring checkpoint/portability from Tier was rejected against
  the recon's own finding that `automation_runs` breaks that inference; a uniform `book_id`
  was rejected as dead weight none of the 9 stores' data needs; a DB-native corrupt-data
  tolerance mode was rejected as solving a failure class the DB layer doesn't have.
- **Driving artifacts:** `docs/superpowers/specs/2026-08-05-storage-engine-sqlite-postgres-design.md:196-197`
  (Tier-2 append-only-log membership); `docs/superpowers/plans/2026-08-08-storage-plan4c-logs.md`
  (Design Notes 1-4, the per-store shape table); `.superpowers/sdd/2026-08-08-storage-plan4c-logs/`
  task 1-4 briefs and reports (schema, absorb, portability, the rehearsal this document's
  evidence paragraph summarizes); ADR `2026-08-08-planning-stores-on-payload-tables.md` and
  `2026-08-09-investment-stores-on-payload-tables.md` (the cache-vs-user-data axis this ADR's
  Tier-vs-checkpoint axis extends, and the rehearsal-evidence-in-the-ADR shape this document
  mirrors); the pre-existing `corrections`/`asset_brief` checkpoint-exclusion entries in
  `ai/checkpoint.py` (the file-level-kept-alongside-table-level precedent every new entry here
  mirrors).
- **Why this way (one line):** a store's Tier answers "is this book-scoped user money/planning
  data", and a store's checkpoint/portability answers "should a rewind or export touch this" —
  two genuinely different questions that seven prior stores happened to answer identically,
  which is not the same as them being the same question, and `automation_runs` is the store
  that proves it.
