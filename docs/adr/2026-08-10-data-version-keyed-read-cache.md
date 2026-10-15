---
title: A data-version-keyed read cache in front of the expensive money reads
status: Accepted
date: 2026-08-10
deciders: repo owner ("we are a localhost app, one user; nothing should be slow; something is set up wrong"), Claude (perf/latency-fixes)
---

# 2026-08-10. A data-version-keyed read cache in front of the expensive money reads

## Context

Follows `2026-08-10-perf-is-over-fetch-not-sql-or-workers`. Even after the price-map fix, the
tabs were still slow because of the deeper systemic cause: **nothing was cached.** `net_worth`,
`net_worth_history`, `networth_stats`, `portfolio_analytics`, `coverage`, `build_projection`,
`budget_aggregates`, `compute_brief` and `positions.hydration_context` each recompute from ~2k
transactions + ~200 instruments on EVERY request, and a single tab-load fires ~10 of them at
once. Measured single-threaded, no flow exceeds ~250ms; measured live under the concurrent
fan-out, individual endpoints hit 6-14s. The gap is contention — the GIL serialising a pile of
redundant recompute — not the calculation or the database.

The owner's framing was decisive and correct: a single-user localhost app should not recompute
its whole world on every click. The fix is to compute once and reuse until the data changes —
i.e. a cache. The only hard part is doing that in front of MONEY reads without ever serving a
stale number.

## Decision

Add `backend/db/read_cache.py`: a process-wide memo keyed on an **epoch** that is provably safe
as a money-read cache key:

    epoch = (SQLite PRAGMA data_version, core.io.file_write_epoch(), local date)

- **`PRAGMA data_version`** on a persistent probe connection changes only after ANOTHER
  connection commits, and — verified by test — only once that commit is durable and visible (a
  read that sees the new version already sees the new rows). So a DB write invalidates the cache
  **post-commit**, with no "new key over old data" window. This is why the naive alternatives
  were rejected: bumping a counter in the pre-write hook, or in SQLAlchemy's `commit` event, both
  fire BEFORE the data is visible (tested), which can publish a stale value under the new key.
- **`file_write_epoch()`** (a counter in `core.io`, bumped AFTER each atomic file write lands)
  covers the file-backed stores `data_version` cannot see: `profile.json` (base currency),
  `fx_cache`, skills. A base-currency change busts every money cache.
- **Local date** invalidates date-dependent reads (projection-from-today, month-to-date,
  "today's movers") at the day boundary even when no write happened overnight.

Properties: single-flight (concurrent misses compute once and share — this is what collapses the
fan-out); deepcopy on return (a caller mutating its copy cannot corrupt the cached value);
SQLite-only (Postgres/CI bypass — correctness over speed on an engine we do not ship);
engine-scoped (`dispose_engine()` resets it, and an engine-identity change auto-resets, so a
stale epoch from another database can never be reused — test-isolation safe).

## Consequences

- Verified on the owner's data copy: every cached result is **byte-identical** to its uncached
  (`__wrapped__`) computation; a real ledger write invalidates and recomputes; repeat-load
  latency is <1ms; the **warm concurrent tab-fan-out is 2.3ms (was ~2400ms in-process, 6-14s
  live)**. 1039 service tests + 5 new `tests/db/test_read_cache.py` green.
- **Warm vs cold.** Any reload with no intervening write is ~2ms (all hits). The FIRST load after
  a write is ~1.6s: all endpoints miss at once and each still runs its own heavy fold
  (monthly balance reconstruction is O(months × accounts × txns)); single-flight coalesces the
  shared inputs (`hydration_context`) but not the distinct per-endpoint folds. This is a big
  improvement over 6-14s but is not yet <10ms cold.
- **Over-invalidation is by design (correctness > hit-rate).** `data_version` bumps on ANY
  commit, including telemetry/logs writes that don't affect money reads, so a background
  scheduler write can cold-start the next read. Accepted: over-invalidation only costs a
  recompute, never correctness. A money-aware invalidation (per-domain versions) is a possible
  follow-up if scheduler write frequency makes cold loads common.
- **Follow-ups (not done):** (1) drive the COLD fan-out toward <100ms by caching the
  balance-series primitives or making the monthly folds incremental; (2) money-aware
  invalidation so telemetry writes don't bust money caches. Both are optimizations on top of a
  correct baseline.
- **What was deliberately NOT done:** move money math into SQL aggregates; add uvicorn
  workers/uvloop; enlarge the pool. The systemic problem was recompute-without-caching, and the
  cache addresses it directly.
