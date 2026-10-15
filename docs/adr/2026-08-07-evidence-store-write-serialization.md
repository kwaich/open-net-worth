---
title: EvidenceStore's DB-backed writes serialize under one process-wide lock, matching the JsonStore-era guarantee exactly
status: Accepted
date: 2026-08-07
deciders: Claude (implementer agent, storage plan 1 task 7) + repo convention (CLAUDE.md write-choke rules)
---

# EvidenceStore's DB-backed writes serialize under one process-wide lock, matching the JsonStore-era guarantee exactly

## Context

Storage plan 1 task 7 reimplements `backend/db/evidence_store.py`'s `EvidenceStore` on SQLAlchemy
Core, same class name and method signatures, callers untouched. The parity contract is binding:
"same as the JsonStore behavior exactly."

The old `JsonStore` base class held a **process-wide, path-keyed `threading.Lock`**
(`json_store._lock_for(path)` — a module-level `dict[str, _DataWriteLock]`) for the full
read-modify-write cycle of every mutating method, including `merge_hits` and `prune`. Because the
lock is keyed by path rather than by instance, it serializes ALL `EvidenceStore` instances pointed
at `evidence.json` — not just calls through one object.

This is exercised for real: `monitoring/intel_agent.py` builds ONE `EvidenceStore()` and fans it
into `asyncio.gather(gather_rss(rss_feeds, store=evidence_store), gather_rss(opp_rss_feeds,
store=evidence_store))`. `monitoring/collect.py`'s `gather_rss`/`gather_matched` hop the actual
`record_evidence` call to a worker thread via `asyncio.to_thread` (`_record_and_read`) — so two
real OS threads can call `merge_hits` on the same store concurrently, today, in production.

The Core rewrite's first draft dropped the lock entirely, trusting the database's own transaction
to serialize writers. `merge_hits`'s new-row path computes `next_seq` as a plain
`SELECT COALESCE(MAX(seq), -1) + 1` before `INSERT`ing. Under SQLite's default deferred-transaction
locking, a `SELECT` only takes a SHARED lock — two overlapping transactions can each read the same
`max(seq)` before either escalates to a write lock, then both `INSERT` the same `seq` and one
raises `IntegrityError: UNIQUE constraint failed: evidence.seq`. This reproduced on the FIRST
compatibility-sweep run (`tests/test_opportunity_free_path.py::
test_intel_scan_gathers_opportunity_rss_with_web_search_off`, which drives the real
`asyncio.gather` fan-out) — not a test-setup gap, a genuine parity break: the JsonStore backing
could never fail this way.

## Decision

We will keep a **module-level `threading.Lock()`** in `backend/db/evidence_store.py`
(`_write_lock`), held for the full transaction of both `merge_hits` and `prune`
(`with _write_lock, self._eng().begin() as conn:`). This is the direct equivalent of the old
per-path lock: there is exactly one evidence table, so "one lock per path" collapses to "one lock",
and its scope (in-process thread-safety, not cross-process) matches the old guarantee exactly — the
old lock never protected against multiple OS processes sharing the file either.

A regression test (`tests/db/test_evidence_store_db.py::
test_merge_hits_concurrent_writers_no_seq_collision`) fires 24 concurrent `merge_hits` calls
against one store via a `ThreadPoolExecutor` and asserts no collision and a contiguous `seq`
sequence. Verified to fail reliably (reproducing the exact `IntegrityError` above) with the lock
removed, and pass with it in place.

## Consequences

### Positive
- Exact behavioral parity with the JsonStore era for the one caller pattern that actually exercises
  concurrent writes today (`run_intel_scan`'s dual `gather_rss` fan-out).
- Zero risk to SQLite's WAL read/write concurrency: only the two write paths take the lock; `all`,
  `find_by_id`, `count`, and `for_subjects` stay lock-free reads, unchanged from before.
- Contained entirely to `evidence_store.py` — no change to `backend/db/engine.py` or any other
  store, so it doesn't presume how a LATER JsonStore-to-DB cutover (plan 1 has more coming) should
  handle its own concurrency.

### Negative
- This is in-process only. If the app ever runs multiple worker processes against one SQLite file,
  this lock provides no protection — but neither did the code it replaces, so this is a pre-existing
  limit carried forward, not a new one.
- A future DB-backed store with the same "compute-a-sequence-then-insert" shape will need to make
  the same call (or a stronger one, e.g. `BEGIN IMMEDIATE` at the engine level, or a DB-native
  identity column) — this ADR documents the pattern and the reasoning but does not generalize it.

### Follow-ups
- If/when a later plan-1 task moves a JsonStore with the same "concurrent writers share one
  instance across `asyncio.to_thread`" shape to the DB, revisit whether a per-store lock (this
  approach) or an engine-level `BEGIN IMMEDIATE` policy (rejected below, for evidence specifically)
  is the better fit — the two are not mutually exclusive project-wide.

## Alternatives considered

- **SQLite `BEGIN IMMEDIATE` via an engine-level "begin" event listener** (the standard
  SQLAlchemy+pysqlite recipe: `isolation_level = None` at connect, `conn.exec_driver_sql("BEGIN
  IMMEDIATE")` on every begin) — rejected for THIS task. It's the more broadly-correct fix (also
  covers multi-process writers, and generalizes to every future DB-backed store), but it lives in
  `backend/db/engine.py`, outside this task's assigned file, and — more importantly — SQLAlchemy's
  "begin" event fires on `Engine.begin()` AND on autobegin-on-first-execute for a bare `.connect()`,
  so it would force EVERY read (`all`, `for_subjects`, …) to also take a write-intent RESERVED lock,
  serializing readers against each other and throwing away the read/write concurrency WAL mode was
  turned on for. Worth reconsidering as a deliberate, scoped, engine-level decision later — not as a
  side effect of one store's bug fix.
- **A scalar subquery inside the INSERT** (`INSERT INTO evidence (..., seq, ...) VALUES (...,
  (SELECT COALESCE(MAX(seq),-1)+1 FROM evidence), ...)`), relying on SQLite executing the whole
  statement as one atomic unit — rejected: correctness would depend on unverified SQLite internal
  lock-escalation timing (whether the scalar subquery evaluates before or after the write lock is
  acquired). Not worth staking a money-adjacent app's data integrity on an unconfirmed engine
  behavior when a proven, well-understood primitive (`threading.Lock`) was available.
- **Retry-on-IntegrityError** — rejected: papers over the race instead of removing it, and adds
  nondeterministic retry behavior to a code path that used to be simply race-free.
- **Do nothing / relax the failing test** — rejected outright: the task's own binding instruction is
  that an assertion failure from a genuine store defect must be fixed in the store, never in the
  test. The failure was reproducible, understood, and traced to a real gap versus the documented old
  behavior.

## Agent provenance

- **Decided by:** Claude (implementer agent), storage plan 1 task 7 lane (`lane/storage-plan1`
  worktree).
- **Alternatives weighed:** see above — `BEGIN IMMEDIATE` (engine-level) was the strongest
  competitor and is explicitly left as a live option for a future, broader decision rather than
  ruled out for the whole app.
- **Driving artifacts:** `.superpowers/sdd/2026-08-07-storage-plan1-substrate-and-evidence/task-7-brief.md`
  (binding parity contract); found via the brief's own mandated compatibility sweep, not speculative
  hardening.
- **Why this way (one line):** the smallest change that restores the EXACT concurrency guarantee the
  callers already depended on, without silently changing WAL's read-concurrency behavior for every
  other query in the store.
