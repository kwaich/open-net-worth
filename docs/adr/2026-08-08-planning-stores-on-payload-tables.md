---
title: Planning stores on payload tables — the book_payload_table()/BookPayloadTableStore primitive, the two hand-rolled stores, money-in-payload-is-exact, no cross-table FK between planning tables, corrections checkpoint-exclusion, and the income_events portability fix
status: Accepted
date: 2026-08-08
deciders: Claude (implementer agents, storage plan 4a tasks 1/2/3/4/7/8 — schema, stores, absorb, portability, rehearsal), repo owner (spec book_id ruling; D6 cutover-strategy ruling — per-store round-trip verify, rename not delete)
---

# 2026-08-08. Planning stores on payload tables

## Context

Storage plan 4a Wave A moved all 12 Planning stores — `category_groups`, `categories`,
`commitments`, `envelopes`, `income_events`, `recurring_streams`, `targets`, `corrections`,
`match_decisions`, `match_reviews`, `budget_assignments`, `plan_state` — off `JsonStore` and
onto SQLAlchemy Core + Alembic (design spec
`docs/superpowers/specs/2026-08-05-storage-engine-sqlite-postgres-design.md`, D4/D5). Three
things were already decided before a line of schema was written:

- The spec's own book_id ruling (spec:137–139/445): planning is Tier-1 (book-scoped, unlike
  Tier-2 reference data), so every planning table needs a `book_id` column and a book-leading
  index — the identical shape the money core (plan 3) already established.
- Money's own ADR (`2026-08-08-money-core-tables-verbatim-payload-plus-promoted-columns.md`)
  had just settled the "payload vs. promoted columns" question for money data, driven by real
  evidence that a promoted-only column cannot distinguish "key absent" from "key present but
  empty" on live rows. Planning's 12 stores needed the SAME question asked and answered on
  their OWN shapes, not assumed to transfer automatically.
- Plan 3 task 4's decimal-Text pattern (`Float` → `Text` holding `repr(float)`-exact strings,
  revision 0008) existed as a reusable primitive (`money_io._money_text`) for whichever
  planning field, if any, turned out to need the same treatment.

## Decision

We will:

1. **Extract a reusable book-scoped primitive, `book_payload_table()`/`BookPayloadTableStore`,
   via a behavior-preserving refactor of the existing `PayloadTableStore`.** Every one of
   `PayloadTableStore`'s four INSERT sites (`insert`, `bulk_insert`, `upsert`'s insert branch,
   `replace_all`'s insert branch) now builds its row through one overridable
   `_insert_row(seq, id_val, item)` helper; the DEFAULT body is byte-identical to the
   pre-refactor inline `{"seq", "id", "payload"}` dict (proven directly by
   `test_generic_payload_store_unaffected_by_refactor` — Tier-2 `instruments`, still on plain
   `payload_table()`/`PayloadTableStore`, carries no `book_id` column at all). `BookPayloadTableStore`
   overrides only that one method to add `book_id=DEFAULT_BOOK_ID`. **10 of the 12 planning
   tables** — `category_groups`, `categories`, `commitments`, `envelopes`, `income_events`,
   `recurring_streams`, `targets`, `corrections`, `match_decisions`, `match_reviews` — are
   `book_payload_table("<name>")` with no further customization. The primitive was built with
   Wave B (investments) as a NAMED second consumer, not merely a hoped-for one — the DRY
   extraction (one overridable method vs. four duplicated bodies) was a stated design goal of
   task 1's brief before Wave B existed to consume it.
2. **Two stores are hand-rolled, for two different, genuine shape reasons — not because the
   primitive was insufficient in general:**
   - `budget_assignments` is a real relational table: `(book_id, month, category) -> amount`,
     keyed by a deterministic, book-prefixed surrogate PK
     (`f"{book_id}:{month}:{category}"`, all three components colon-free so two books can
     never collide), with **no `seq` column** — its JsonStore-era source was an unordered
     `{month: {category: amount}}` dict, so there is no insertion order to preserve.
   - `plan_state` is a one-row-**per-book** singleton: the PK **is** `book_id` itself (today,
     one row, `book_id = DEFAULT_BOOK_ID`), payload the whole `{scenarios, baseline}` dict
     verbatim.
3. **Money-in-payload-is-exact: only `budget_assignments.amount` is promoted to a typed
   `Text` column holding `repr(float(...))`-exact decimal** (revision-0008's pattern, reused
   verbatim via `money_io._money_text` — the one `repr(float)` helper, not reimplemented).
   Every OTHER money-bearing planning store — `targets`, `commitments`, `income_events`,
   `recurring_streams` — keeps its money fields **inside the verbatim `payload` JSON column**,
   where Python's shortest-round-trip float encoding already makes `export == source` exact
   without any promotion machinery. This is the opposite of money's own "promote for query
   surface, keep payload for round-trip truth" pattern on purpose: money promotes columns that
   are QUERIED relationally (`from_account_id`, `date`, …); no planning store's non-assignment
   money field is ever queried or summed at the SQL layer — every planning read (`budget_month`,
   `hydrate`, `coverage`, `income_summary`, …) loads whole rows in Python and does its own
   arithmetic there, exactly as it did against JsonStore. Promoting a column nothing queries
   would be duplicate storage for zero exactness gain. `budget_assignments.amount` is the one
   exception because it genuinely IS queried relationally (`month_map(month)`,
   `all_map()`'s per-month fold) — the same test money's own ADR uses to justify promotion in
   the first place.
4. **No cross-table FK between any of the 12 planning tables.** `categories`/`envelopes`/
   `category_groups` reference each other by OPAQUE id only (`envelope_id`, `category_ids`,
   `group_id`) — loose coupling is deliberate, mirroring the pre-existing
   envelope-id-native design (ADR `2026-07-05-envelope-id-native.md`). The only FK on any of
   the 12 tables is `book_id -> books.id`, the identical shape the money core uses. `tables.py`
   records the ruling inline: *"NO cross-table FK BETWEEN planning tables (categories/envelopes/
   groups referenced by OPAQUE id only — loose coupling is deliberate; the only FK is
   `book_id -> books.id`, matching the money core)."*
5. **`corrections` is checkpoint-EXCLUDED** (`ai.checkpoint._EXCLUDED_TABLES`) — a
   runtime/behavioral signal log (learn-from-corrections rule suggestions), not user-authored
   data, mirroring `txn_audit`'s existing exclusion rationale: a `/rewind` must not resurrect
   stale correction history. This replaces the old file-level `CORRECTIONS_JSON` exclusion in
   `_EXCLUDED_PATHS` (kept there too, for the now-legacy, post-cutover file).
6. **The `income_events.json` portability fix — real user data must be in `PORTABLE_FILES`,
   not just `BRIDGED`.** Task 7's first pass wired all 12 planning filenames into
   `portable_bridge.BRIDGED` (so `/api/reset`'s `wipe_all` reaches every one of them) but added
   only 8 to `core.portable.PORTABLE_FILES` (the manifest `/api/reset`'s PRE-WIPE BACKUP loop
   actually iterates) — `income_events.json` was wired into the wipe path without being wired
   into the backup path, a real, reproducible data-loss Blocker a review caught: a self-hoster
   hitting reset would silently lose their entire dividend/interest/distribution income
   history, with no backup and no undo (`/api/reset` opens no checkpoint turn). Fixed by adding
   `INCOME_EVENTS_JSON` to `PORTABLE_FILES`. The fix forced a real classification of all 12
   stores, not just the one bug:

   | # | Store | Checkpoint | Nature | `PORTABLE_FILES` |
   |---|---|---|---|---|
   | 1 | `category_groups` | INCLUDED | real user data (planning config) | LIVE |
   | 2 | `categories` | INCLUDED | real user data (category registry) | LIVE |
   | 3 | `commitments` | INCLUDED | real user data (financial commitments) | LIVE |
   | 4 | `envelopes` | INCLUDED | real user data (budget envelopes) | LIVE |
   | 5 | `recurring_streams` | INCLUDED | real user data (cash-flow streams) | LIVE |
   | 6 | `targets` | INCLUDED | real user data (savings targets) | LIVE |
   | 7 | `budget_assignments` | INCLUDED | real user data (assigned money) | LIVE |
   | 8 | `plan_state` | INCLUDED | real user data (FIRE scenarios) | LIVE |
   | 9 | `income_events` | INCLUDED ("NOT a cache" — own docstring) | **independent historical fact** (dividend/interest/distribution income; true regardless of whether the account/position that produced it still exists) | **LIVE (this fix)** |
   | 10 | `corrections` | EXCLUDED | operational: behavioral signal log, not user-authored | DORMANT |
   | 11 | `match_reviews` | INCLUDED | operational: workflow queue keyed on a transaction-id PAIR that `/api/reset` ALSO wipes (the money six) — meaningless once that pair is gone | DORMANT |
   | 12 | `match_decisions` | INCLUDED | operational: verdict cache, same transaction-id-pair coupling as #11 | DORMANT |

   The distinguishing test between row 9 and rows 11–12 is not checkpoint status alone
   ("checkpoint-included real user data" is a conjunction, not a single bit) — it is whether
   the store is an **independent historical fact** versus **workflow/derived state
   referentially coupled to data the same reset already destroys in the identical pass**.

**Verified at production scale (2026-08-08 dress rehearsal 4A, a scratchpad clone of the
owner's real data, live tree untouched and read-only throughout):** all 12 planning sources —
9 category_groups, 38 categories, 6 commitments, 43 envelopes, 1 income_event, 0
recurring_streams (none declared/detected yet on this book), 1 target, 227 corrections, 27
match_decisions, 58 match_reviews, 1 budget_assignment row (1 month), 3 plan_state scenarios —
absorbed via the real `ensure_migrated()` chain 0001→0010. Six planning-derived figure
families — `budget_month()`'s per-envelope assigned/available/ready-to-assign across every
month carrying assignment data, envelope balances, `targets.list_hydrated()`'s
required-monthly/pace, `commitments.coverage()`'s verdict, the declared-stream projection
(`RecurringStreamStore.declared()` → `recurring.stream_month_amounts`), and
`income.income_summary()` for every position — computed on this migrated clone hashed
**byte-identical** (sha256 `8f2090e3…2a5ac2e7`, 19,171 bytes) to the SAME six families computed
by the pre-cutover (SHA `d49f7595`, the commit immediately before task 1) service+store code
running against an identically-built, still-JSON-planning copy of the same source data — the
owner's own "bet my real numbers on it" test, executed by machine, one layer above money's own
rehearsal 3. A live `/rewind` proof (`set_assigned` a real `(month, category)` via
`planning.apply_budget_change`, edit a real commitment's `note` via
`commitments.update_commitment`, both inside one `begin_turn`/`end_turn` pair) reverted both
rows to a byte-exact match of their pre-edit state via `ai.checkpoint.restore(mode="data")`,
confirming `corrections`' exclusion is the ONLY planning-table gap in row-level undo — every
other planning write, including the two exercised here, rides the SAME generic `write_log`
mechanism money's own rehearsal proved. Full mechanism and evidence:
`.superpowers/sdd/2026-08-08-storage-plan4a-planning/task-8-report.md`.

## Consequences

### Positive

- The book-scoped primitive paid for itself immediately: 10 of 12 tables needed zero bespoke
  schema or store code, only a schema-file registration line and a store-file subclass
  declaration — the DRY bet from task 1 held.
- Money-in-payload-is-exact means planning's schema footprint stays small: only ONE column in
  the entire 12-table unit needed the decimal-Text treatment, versus money core's seven —
  because planning has exactly one genuinely-relational money field and the rehearsal's
  byte-identical parity hash is the structural proof the other four money-bearing stores lost
  nothing by staying JSON-verbatim.
- Loose (opaque-id-only) coupling between planning tables means a future planning table can be
  added, removed, or independently re-shaped without a cross-table migration — the same
  flexibility the JsonStore era had, now with `book_id` partitioning and row-level checkpoints
  added on top rather than traded away.
- The `income_events` fix closes a real, reproducible data-loss bug before it ever reached the
  live tree (found by review during THIS wave, not by a self-hoster after ship).

### Negative

- The book_id-scoped-but-unfiltered-on-read behavior (inherited from the money core's own
  `AccountStore` precedent) means every planning store, like every money store, is not yet
  actually multi-tenant-safe — it is partition-KEYED, not partition-ENFORCED. Deferred
  identically to the money core's own deferral, not a new gap this wave introduces.
- `corrections` being checkpoint-excluded means a `/rewind` cannot undo a wrongly-suppressed
  or wrongly-recorded correction signal — an accepted, pre-existing trade (mirrors `txn_audit`)
  the store's own docstring already documents, not a new limitation from this wave.
- The 12-store classification table above is a snapshot; a FUTURE planning store must be
  independently re-classified against the same two-axis test (independent historical fact vs.
  referentially-coupled workflow state) — nothing in the code enforces that a new store's
  author actually runs this check.

### Follow-ups

- Wave B (investments) is the named second consumer of `book_payload_table()`/
  `BookPayloadTableStore` — any friction it finds should feed back into the primitive's
  docstring, not fork a second, slightly different helper.
- The residual category-delete/automations atomicity window (categories + category_groups +
  transactions land atomically; the automation-instruction rewrite that follows does not) is
  tracked in `docs/launch/BOARD.md`'s inbox and closed properly by the companion ADR
  (`2026-08-08-planning-choke-points-compose-in-one-transaction.md`), not here.

## Alternatives considered

- **Promote every planning money field to a typed column, mirroring money core's seven** —
  rejected: no planning read queries or sums a non-assignment money field at the SQL layer
  (every one is loaded whole-row and computed in Python), so promotion would add schema and
  write-time cost for a query pattern that does not exist. The rehearsal's byte-identical
  parity hash is the empirical check this reasoning was correct, not merely asserted.
  `budget_assignments.amount` is promoted precisely because it IS the one field genuinely
  queried relationally (`month_map`).
- **Give `book_payload_table()` a cross-table FK from `envelopes.category_ids`/
  `categories.group_id` into their referenced tables** — rejected: these are conceptually
  many-valued and precedence-based (a category's group membership can be legitimately absent
  during the create-then-place sequence a single `create_category` call performs), and the
  JsonStore era never enforced this referentially either; a DB constraint here would block a
  legal transient state the two-write choke (see the companion ADR) already had to reason
  about directly.
- **Route `income_events.json`'s fix through a broader "re-audit all portability policy"
  task** — rejected as scope creep beyond what the actual bug required: the fix is a one-line
  `PORTABLE_FILES` addition (mirroring the identical shape ADR
  `2026-08-07-portable-export-bridges-db-backed-stores.md` already used for
  price_history/instruments, and Plan 3 task 6 used again for `TXN_AUDIT_JSON`/
  `MERGE_JOURNAL_JSON`), not a reason to re-litigate the other 11 stores' already-correct
  classification.

## Agent provenance

- **Decided by:** Claude (implementer agents across storage plan 4a tasks 1 [primitive +
  schema], 2 [6 simple stores + corrections exclusion], 3 [4 money-in-payload stores], 4
  [budget_assignments + plan_state], 7 [portability + the income_events fix]; recorded by the
  task 8 implementer agent, which additionally ran the production-scale rehearsal cited above
  as independent verification of every claim in this ADR). Lane/branch: `lane/storage-plan1`
  (worktree `storage-plan1`).
- **Alternatives weighed:** full column promotion for every planning money field was rejected
  against the "nothing queries it relationally" test, verified empirically by the rehearsal's
  parity hash rather than merely argued; a cross-table FK on category/envelope/group
  references was rejected as incompatible with the create-then-place transient states the
  choke seam already has to reason about; broadening the income_events fix into a full
  portability re-audit was rejected as scope creep beyond the actual reproduced bug.
- **Driving artifacts:** `docs/superpowers/specs/2026-08-05-storage-engine-sqlite-postgres-design.md`
  (D4/D5, book_id spec:137–139/445); ADR
  `2026-08-08-money-core-tables-verbatim-payload-plus-promoted-columns.md` (the payload-vs-
  promoted precedent this ADR extends and partially declines to follow, with reasons); ADR
  `2026-08-08-money-stored-as-exact-decimal-text-arithmetic-stays-float.md` (the
  `_money_text`/decimal-Text mechanism `budget_assignments` reuses); ADR
  `2026-08-07-portable-export-bridges-db-backed-stores.md` (the `PORTABLE_FILES`-vs-`BRIDGED`
  distinction the income_events bug violated); plan 4a task 1/2/3/4/7 reports and their review
  packages (`.superpowers/sdd/2026-08-08-storage-plan4a-planning/`); this task's own rehearsal
  report (`.superpowers/sdd/2026-08-08-storage-plan4a-planning/task-8-report.md`).
- **Why this way (one line):** a schema decision (promote vs. payload, FK vs. opaque id,
  checkpoint-include vs. exclude, portable vs. dormant) is only correct if it matches how the
  data is ACTUALLY read and reset today, not how a sibling tier happened to decide it — so
  each of the five decisions above was checked against planning's own real query/reset/restore
  paths rather than copied wholesale from the money core's.

## Deferred Wave-A minors folded into this task

Two pre-existing, disclosed-but-deferred minors (both flagged "harmless," neither a money bug)
were quick enough to fold in here rather than carry further:

- `tests/db/test_money_decimal.py`'s poisoned-downgrade test had a stale trailing comment
  ("still at head") left over from task 1's revision-chain shift (0008 was head before
  revision 0009 landed, then wasn't) — corrected to "still at 0008", matching the assertion
  two lines below it. No behavior change.
- `tests/test_planning_stores.py`'s `groups_store` fixture set a dead `._path =` attribute on
  a `CategoryGroupsStore` instance — a JsonStore-era isolation idiom the store (DB-backed
  since task 2) no longer has any attribute named `_path` to honor; the assignment was a
  silent no-op, relying on an unrelated autouse fixture for isolation instead. Replaced with
  the same `db_isolated` fixture the file's sibling `assign_store` fixture already uses —
  genuine per-test DB isolation, not merely a cosmetic rename. Both fixes verified green
  (`tests/test_planning_stores.py tests/db/test_money_decimal.py` — 25/25 passed).
