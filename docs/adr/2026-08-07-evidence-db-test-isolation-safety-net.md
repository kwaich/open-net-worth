---
title: The shared default test database is migrated once and cleared per test, so an un-isolated EvidenceStore() read degrades to empty instead of erroring
status: Accepted
date: 2026-08-07
deciders: Claude (implementer agent, storage plan 1 task 7) + repo convention (tests/conftest.py's existing global-state-isolation pattern)
---

# The shared default test database is migrated once and cleared per test, so an un-isolated EvidenceStore() read degrades to empty instead of erroring

## Context

Storage plan 1 task 7 moves `EvidenceStore` off `JsonStore` onto SQLAlchemy Core. Under the old
backing, a test that never isolated its own `evidence.json` path silently read the shared
`_TEST_ROOT/data/evidence.json` — and because `JsonStore.all()` on a missing file returns `[]`,
that was almost always indistinguishable from "no evidence", so most tests never needed to think
about isolation at all. Under DB backing, `EvidenceStore()` resolves lazily via
`backend.db.engine.get_engine()`, and a missing/unmigrated `evidence` TABLE is a hard
`OperationalError: no such table: evidence` — not an empty read.

`tests/db_test_engine.isolate_evidence_db` (added in this same change) fixes every test whose
SETUP could be found by grepping for `EvidenceStore`/`EVIDENCE_JSON` — but the compatibility sweep
turned up two tests that reach `EvidenceStore()` with **zero textual footprint to grep for**:
- `tests/test_news_latency.py::test_lag_stats_empty_store` — calls `lag_stats()` directly, no
  fixture at all.
- `tests/test_collect_max_age.py` (three tests) — call `monitoring.collect.gather_rss(feeds, ...)`
  with no `store=` argument; the file never mentions `EvidenceStore` or `EVIDENCE_JSON` anywhere.
- `tests/test_api_news_sources.py` — a `TestClient(app)` hitting `GET /api/intel/sources`, which
  transitively reaches `EvidenceStore()` through `news_sources.list_sources` → `news_latency.
  lag_stats`.

None of these tests care about REALISTIC evidence data — they only need the read not to explode.
Chasing down every such transitive path by grep is unbounded (the same shape could recur in any
future test that exercises intel/briefing/news-source code without thinking about evidence at
all) — exactly the class of problem `tests/conftest.py`'s existing "Global-state isolation" section
already exists to solve for other shared stores (categories.json, news_sources.json, profile.json,
payees.json, txn_audit.json, accounts+transactions.json).

## Decision

We will add two additions to `tests/conftest.py`, following that section's established shape:

1. **`_migrate_default_evidence_db`** — a `scope="session", autouse=True` fixture that runs the
   evidence-table migration ONCE, against whatever `database_url()` resolves to with no
   per-test override (the throwaway `DATA_DIR/onw.db` the root DATA_DIR redirect already
   guarantees is not the live tree). This makes the shared default path QUERYABLE — restoring the
   old "missing = empty" default — without ever populating it.
2. **A per-test cleanup clause appended to `_reset_global_fx_and_profile`'s teardown** — `DELETE
   FROM evidence` against the default engine, wrapped in the same `try/except Exception: pass` used
   by every other clause there. `evidence_subjects` cascades via FK, so clearing the parent table
   is enough. A test that DID isolate its own database via `isolate_evidence_db` never touches this
   shared table (that helper monkeypatches `get_engine` itself, not the shared singleton), so this
   is a no-op for it — the clause exists only to catch a straggler that both lacks isolation AND
   writes real rows, a case not yet observed but not proven impossible either.

This is explicitly a SAFETY NET, not a substitute for the targeted fixes: every test whose SETUP
could be identified was still adapted directly (see the task-7 report for the full list). The net
exists for what grep structurally cannot find.

## Consequences

### Positive
- Closes an entire class of undiscoverable test breakage — any current or future test that reaches
  evidence-backed code transitively, without its own isolation, degrades to "empty store" instead
  of a hard SQL error, matching pre-cutover behavior.
- One-time cost (a single migration at session start), not per-test — negligible against a
  12,000+-test suite (measured: full suite run time unaffected within noise).
- Matches an already-established, documented convention in the same file rather than inventing a
  new mechanism.

### Negative
- It is a broad, cross-cutting change to a file every test in the suite depends on — higher blast
  radius than a task scoped to one store's file would otherwise touch. Justified here because two
  of the three tests it fixes could not have been fixed any other way (see Alternatives).
- The safety net only helps READS degrade gracefully. A test that both lacks isolation AND
  genuinely WRITES meaningful evidence data would still be silently sharing state with whatever
  else touches the same untouched default path within that one test's execution — the per-test
  DELETE clears it AFTER, not the moment two such tests would collide DURING a run. No test in the
  suite currently does this (verified: the full "not integration" suite is green), but it is a real
  residual gap this ADR records rather than papers over.

### Follow-ups
- The NEXT plan-1 JsonStore-to-DB cutover should re-evaluate whether this same safety-net shape
  (migrate once, clear per test) is worth generalizing into a shared helper rather than
  copy-pasted per store, once there is a second data point.

## Alternatives considered

- **Chase every transitive caller via grep/full-suite runs, fix each file's SETUP directly, add no
  conftest change.** This is what was attempted first. It found and fixed 15 files this way, plus
  `test_api_news_sources.py` and `test_collect_max_age.py` were found only by running the FULL
  suite (not grep) — meaning a straggler that neither greps nor gets exercised in this task's own
  test run would ship undetected. Rejected as insufficiently robust for "the entire suite green,
  attributable failures must be fixed" — a standard that has to hold for tests not yet written too.
- **Migrate AND seed realistic data into the shared default DB.** Rejected: several tests
  (`test_get_sources_row_shape_has_latency_and_last_ok_at_and_failure_streak`, `test_lag_stats_
  empty_store`) assert on the EMPTY case specifically; seeding would flip their assertions.
- **A per-test (function-scoped) migration instead of session-scoped.** Rejected: strictly more
  expensive for no behavioral difference — the schema does not change between tests, only the
  session-level one-time cost is needed.
- **Delete-and-remigrate the shared DB file after each test (mirroring how other JSON files here
  are `.unlink()`ed).** Rejected: unlike a JSON file, a missing SQLite file needs a full migration
  to become readable again — paying that cost every test defeats the point of a "cheap safety net".
  A `DELETE FROM evidence` achieves the same end state (empty) at a fraction of the cost.

## Agent provenance

- **Decided by:** Claude (implementer agent), storage plan 1 task 7 lane (`lane/storage-plan1`
  worktree).
- **Alternatives weighed:** see above; the grep-only approach was tried first and concretely proven
  insufficient (two files found only via a full baseline suite run, not text search) before this
  was added.
- **Driving artifacts:** `.superpowers/sdd/2026-08-07-storage-plan1-substrate-and-evidence/task-7-brief.md`'s
  compatibility-sweep instruction ("any pre-existing test that fails... gets its SETUP adapted");
  extended here to cover tests the sweep's own suggested `-k` filter and a targeted grep both missed.
- **Why this way (one line):** the same "restore the old missing-file-is-empty default, structurally,
  for everyone" philosophy `tests/conftest.py` already applies to every other shared store, applied
  to the first DB-backed one.
