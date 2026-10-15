---
title: json_store.py deleted — the 4-plan JSON→DB storage migration is complete; JsonStore is retired, guarded forever
status: Accepted
date: 2026-08-09
deciders: repo owner (the storage-engine program and its D6 boot-script-cleanup ruling), Claude (implementer agent, storage plan 4e Wave E, tasks 3-4 — the terminal deletion)
---

# 2026-08-09. `json_store.py` deleted — the 4-plan JSON→DB storage migration is complete

## Context

`backend/db/json_store.py` was the original storage primitive for this app: a stateless,
per-file repository (`all`/`find_by_id`/`query`/`count`/`insert`/`bulk_insert`/`upsert`/
`update_by_id`/`delete`/`replace_all`) reading/writing one JSON file per store, with a
per-path lock for concurrency, a read cache keyed on mtime, and — after the F6/F6b hardening
audit — a fail-closed posture on a corrupt file (quarantine + `DataIntegrityError`, never
silent degrade-to-empty). It served the app well past MVP, but carried no cross-store
transaction, no relational query, no dialect choice, and a full-file rewrite on every write.

Spec `docs/superpowers/specs/2026-08-05-storage-engine-sqlite-postgres-design.md` chartered its
replacement: `backend/db/engine.py` (one process-wide SQLAlchemy Core engine, SQLite/WAL
shipped default, Postgres CI-tested from the first Alembic revision) + `backend/db/tables.py`
(one shared `MetaData`) + Alembic migrations under `backend/db/alembic/`, with
`PayloadTableStore` (`backend/db/payload_store.py`) as the generic drop-in replacement for a
flat-list `JsonStore` — same public surface, `(seq, id, payload JSON)` underneath. The cutover
ran store-by-store, wave-by-wave, over four plans, never a big-bang rewrite:

- **Plan 1** — the DB engine/tables/Alembic bootstrap, `EvidenceStore` (the first cutover,
  proving the `(id, payload)` shape and its own write-serialization lesson), and the first pass
  at retiring absorbed-store boot-migration scripts.
- **Plan 2** — `price_history` (real relational columns, not a payload blob) and `instruments`
  (the first `PayloadTableStore`, and the first CHECKPOINT-INCLUDED DB store proven in the
  `/rewind` contract via `core.io.fire_db_write_hook` + row-level `write_log`).
- **Plan 3** — the money core: `accounts`/`transactions`(+`transaction_splits`/
  `transaction_external_refs`)/`txn_audit`/`import_batches`/`statements_meta`/`merge_journal`,
  eight tables absorbed as one referential unit, every money write choke recomposed onto
  `db.engine.locked_unit_of_work` (revisions 0006–0008).
- **Plan 4, five waves** — Wave A: all 12 Planning stores (revisions 0009–0010). Wave B: all 8
  investment stores (0011–0012). Wave C: all 9 logs/telemetry stores, and the discovery that
  Tier (book-scoped or not) and checkpoint/portability are independent axes, not one inferred
  from the other (0013–0014). Wave D: all 12 config stores (0015–0016). Wave E: the last 5
  misc/runtime tables — `artifacts`/`uploads` (hybrid: DB metadata catalog, Tier-4 file bytes),
  `email_snapshots`, `pending_pushes`, `checkpoints` (the turn-checkpoint index; its gzip blob
  pool stays Tier-4 files) — (0017–0019), **then this task**: retiring the last two
  `JsonStore`-adjacent boot scripts and deleting `backend/db/json_store.py` itself.

By the time this task started, `backend/` already had **zero `JsonStore` subclasses and zero
real `json_store` imports** (confirmed by both a plain grep and an AST scan of every
`ClassDef.bases` — every one of the ~103 remaining textual "JsonStore" mentions in `backend/`
was a comment or docstring). The only *real* importers left anywhere in the tree were 7 test
files that exercised `JsonStore` directly (its durability/corruption handling, its read cache,
its concurrency guarantees) or used it incidentally as a fixture. The class had no live callers
— only its own file, and its own tests, kept it from being dead code in truth as well as in
name.

## Decision

**Delete `backend/db/json_store.py` outright, and make its absence a permanent, self-checking
invariant — not a one-time cleanup that can silently regress.**

1. **The 7 test files that imported it were resolved first**, so removing the module wouldn't
   break the suite:
   - Deleted outright (they tested `JsonStore`'s own class behavior, which no longer exists to
     have behavior): `tests/test_json_store_durability.py`, `tests/test_json_store_read_cache.py`,
     `tests/test_json_store_concurrency.py`.
   - Refactored to keep their real assertion while dropping the `JsonStore` dependency:
     `tests/test_seed_examples.py` (swapped a `JsonStore(...).all()` read for the plain
     `json.loads` the rest of the same file already used), `tests/test_migrate_automations_v3.py`
     (its concurrent-writer proof now goes through `core.io.atomic_write_text` directly — the
     actual primitive both `JsonStore` and the migration script itself call — instead of
     instantiating the retired class), `tests/test_checkpoint_hook.py` (its two write-hook tests
     now call `core.io.fire_write_hook`/`atomic_write_text` directly, the same pair every
     remaining file-backed store — `SkillStore`, `AuthStore`, `budgets`'s `profile.json`,
     `portability`'s session bundles — already calls).
   - `tests/db/test_payload_store.py` kept everything except its one "parity cross-check vs a
     real JsonStore" test, which by definition had nothing left to cross-check against; the rest
     of that file (`PayloadTableStore`'s own CRUD/checkpoint/concurrency suite) already stood on
     its own and needed no change.
2. **Two BLOCKING preconditions gated the deletion itself**, run fresh, not trusted from the
   confirmed-state investigation above: an AST scan walking every `ClassDef.bases` under
   `backend/` for a `JsonStore` base (empty), and an import-statement grep across `backend/`
   for every shape of `from`/`import ... json_store` (empty). Either finding anything would have
   stopped the deletion — this is the same discipline `db/migrate.py::ensure_migrated()` applies
   to schema state: verify before doing something destructive, don't infer.
3. **`git rm backend/db/json_store.py`.**
4. **`tests/db/test_no_json_store.py`** is the permanent guard: three tests, each walking
   `backend/` FRESH on every run (never a cached file list), asserting (a) the file does not
   exist, (b) the same AST scan is still empty, (c) the same import grep is still empty. It
   passes today and fails the instant anyone reintroduces the pattern — a plain grep would miss
   a renamed import alias; the AST scan does not.
5. **Comments that made a now-false present-tense claim about an active `JsonStore` caller were
   reworded to the current mechanism** — `core.io.atomic_write_text`'s docstring, `core.errors
   .DataIntegrityError`'s docstring (now pointing at `AuthStore._raise_corrupt` and
   `db.migrate.ensure_migrated()`'s `MigrationError` as the current fail-closed examples),
   `api/investments_xray.py`'s checkpoint-coverage note (now naming `PayloadTableStore`'s
   `write_log` / `core.io.fire_db_write_hook`), `api/skills.py`'s write-path note (now naming
   `SkillStore`'s own direct `fire_write_hook`/`atomic_write_text` calls — it was never a
   `JsonStore` subclass to begin with). Comments framed as history ("JsonStore-era", "formerly",
   "RETIRED — see...") were left alone deliberately; they are still true, as history.
6. **Docs flipped from "in progress" to "complete/retired"**: `CLAUDE.md`'s storage-engine
   bullet and `docs/DATA_MODEL.md`'s storage-engine opening no longer describe a substrate
   mid-cutover — they describe a finished one, plus the deliberately-still-file-based set (Tier
   4 bytes, `auth.json`, `profile.json`, chat-session storage) that was never `JsonStore` and was
   never going to be absorbed. `BACKEND_ARCHITECTURE.md`'s layer diagram and `db/` section, which
   still taught "subclass `JsonStore`" as the way to add a store, now teach `PayloadTableStore`.

## Consequences

### Positive

- **One storage primitive, not two.** Every store in `backend/db/` is now either a
  `PayloadTableStore` (or a hand-rolled table for a shape it doesn't fit — `budget_assignments`,
  `plan_state`, `watchlists`) or a small, explicitly-named set of genuinely file-based stores
  that were never `JsonStore` in the first place. A future engineer — human or agent — has one
  answer to "how do I persist this", not two competing ones with different guarantees
  (transactions, checkpoint integration, query capability).
- **The invariant is self-checking, not just documented.** `tests/db/test_no_json_store.py`
  turns "don't bring this pattern back" from a CLAUDE.md sentence someone might not read into a
  CI-blocking test someone can't miss.
- **Four plans' worth of incremental, store-by-store risk is retired along with the file.** Every
  wave along the way (Plans 1-4e) shipped its own production-scale rehearsal against a clone of
  a representative book before landing; this task closes the file only after every one of those
  waves had already proven its store's cutover, not as a leap of faith on the last day.

### Negative

- **A handful of comments outside this task's explicit scope are now also stale** in the same
  way the four reworded ones were, but were left untouched because they weren't the four files
  named for this task: `services/income.py`, `services/lots.py`, `services/equity.py` (each
  claims a write "funnels through `JsonStore._save_raw` → write hook", but `income_events`/
  `lots`/`grants`/`vest_events` are all DB-backed since Plan 4a/4b), `services/alerts.py` (names
  "the JsonStore write hook" for a store that was never a `JsonStore` subclass to begin with),
  `services/portability.py` (attributes its own direct `fire_write_hook` call to "the JsonStore
  write hook" — functionally accurate, the name is what's stale), and — the most materially
  wrong of the group — `services/categories.py`/`services/envelopes.py`/`services/planning.py`,
  which claim in several places that "automations is still a JsonStore file this wave", when
  `automations` has been DB-backed (`BookPayloadTableStore`) since Plan 4d. These are flagged for
  a follow-up sweep, not fixed here, to keep this task's diff matched to its named scope.
- **`BACKEND_ARCHITECTURE.md`'s "Data access layer" file/store table (its `accounts.json` /
  `AccountStore` / … rows) is far more broadly stale than this task's narrow "does it say
  JsonStore" check catches** — most of the listed `.json` files no longer exist as files at all.
  That table's drift predates this task by several waves and needs its own audit against
  `docs/DATA_MODEL.md` (the doc CLAUDE.md's own docs map already names as the authority for
  store status); not attempted here, since fixing it correctly means re-verifying ~25 stores
  individually, a different-shaped task than a JsonStore-retirement sweep.
- **`.claude/skills/add-store/SKILL.md` still teaches "subclass `JsonStore`" as the way to add a
  store** — verbatim code example included. Left unfixed here (skills are a different artifact
  class than the four docs this task's brief named), but this is the one leftover finding with
  real teeth: an agent that follows that skill today will write code
  `tests/db/test_no_json_store.py` immediately fails on. Flagged as the highest-priority
  follow-up from this task.

### Follow-ups

- Rewrite `.claude/skills/add-store/SKILL.md` for `PayloadTableStore` (a `payload_table(name)`
  factory + an Alembic revision + a subclass), sourced from this document's Plan 2 `instruments`
  precedent and `docs/DATA_MODEL.md`'s matching paragraph. Highest priority of the three
  follow-ups here — it is prescriptive, not descriptive, and wrong in the dangerous direction.
- Audit `BACKEND_ARCHITECTURE.md`'s "Data access layer" table against `docs/DATA_MODEL.md`'s
  per-store cutover record and either correct it store-by-store or replace it with a pointer to
  `docs/DATA_MODEL.md`.
- Sweep the six services-layer files named under Negative above for their own now-false
  "JsonStore" write-path claims.

## Alternatives considered

- **Tombstone the file (keep it, raise `NotImplementedError` on construction) instead of
  deleting it** — rejected: the program's own D6 ruling (`2026-08-08-absorbed-money-era-boot-
  scripts-deleted-not-tombstoned.md`) already settled this shape of question for boot scripts,
  and the reasoning transfers directly — a class with zero live callers and a permanent test
  guarding its absence is not made safer by leaving an inert corpse in the tree; git history
  already preserves it if anyone ever needs to read it again.
- **Skip the AST scan and rely on the import grep alone** — rejected: a plain grep cannot see a
  `class FooStore(json_store.JsonStore)` written after `import backend.db.json_store as
  json_store`, nor a base class reached via an aliased import. The brief's own BLOCKING-gate
  design insisted on the AST walk specifically because grep is a textual proxy for the thing that
  actually matters (does any class inherit from this), not the thing itself.
- **Fix every stale "JsonStore" comment found while grepping `backend/`, not just the four named
  ones** — rejected for this change: the four were the ones a controller had already reviewed
  and named; the ~10 additional stale hits found along the way (see Consequences → Negative) are
  real, but sweeping them without the same review would have expanded this diff well past "the
  terminal deletion task" and risked getting one of them wrong in a file this task's brief never
  asked it to touch. Named explicitly instead, so they aren't lost.

## Agent provenance

- **Decided by:** repo owner (the storage-engine program's originating spec and its D6
  boot-script-cleanup ruling, both of which this task's two commits complete); Claude
  (implementer agent, storage plan 4e Wave E, tasks 3-4 — retiring
  `migrate_drop_payee_txn_count.py` and then the terminal `json_store.py` deletion). Lane/branch:
  `lane/storage-plan1` (worktree `storage-plan1`).
- **Alternatives weighed:** tombstoning was rejected on the same grounds D6 already established
  for boot scripts; grep-only verification was rejected as structurally weaker than the AST scan
  the brief required; sweeping every stale comment found along the way (not just the four named)
  was rejected as scope creep for this specific change, with the finding preserved here instead
  of silently dropped or silently fixed off-brief.
- **Driving artifacts:** `docs/superpowers/specs/2026-08-05-storage-engine-sqlite-postgres-
  design.md` (the program's charter); every ADR listed in this document's Context section
  (Plans 1-4e, store by store); `2026-08-08-absorbed-money-era-boot-scripts-deleted-not-
  tombstoned.md` (the "delete outright, don't tombstone" precedent this task's Task 3 also
  applied to `migrate_drop_payee_txn_count.py`); `.superpowers/sdd/2026-08-08-storage-
  plan4d-configs/wave-e-terminal-brief.md` (this task's own brief, with the controller's
  pre-decided rulings on the two boot scripts and the ordered preconditions for the deletion);
  `tests/db/test_no_json_store.py` (the guard this decision is only as good as).
- **Why this way (one line):** a storage primitive with zero live callers, four plans of
  store-by-store evidence that nothing still needs it, and a permanent test guarding its
  non-reappearance is safe to delete outright — the risk this task manages is not "should it go"
  but "prove nothing depends on it first, in an order that cannot skip a step."
