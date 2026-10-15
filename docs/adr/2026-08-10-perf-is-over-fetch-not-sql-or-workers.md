---
title: The app's slowness was hot-path over-fetch, not a need for SQL aggregates or more workers
status: Accepted
date: 2026-08-10
deciders: repo owner (diagnosis direction — "we are just a localhost app, one user; Python should serve this well; maybe we are setting up something wrong"), Claude (perf investigation on perf/latency-fixes)
---

# 2026-08-10. The app's slowness was hot-path over-fetch, not SQL aggregates or more workers

## Context

After the JSON→DB migration shipped, the app felt slow — seconds to open almost any tab
(accounts, commitments, scheduled, net worth). The tempting reads of that symptom were the
expensive ones: rewrite the money-bearing reads as in-database SQL aggregates, or scale the
process model (uvicorn workers / uvloop / a bigger connection pool). The owner rejected both as
the *default* move: this is a single-user localhost app with ~2k transactions and ~200 priced
instruments; Python has no business being slow at that size, so the real cause was more likely
"something is set up wrong" than "the language / the one process can't keep up."

Measurement bore that out. In isolation every flow is fast — `transactions.all()` 29 ms,
`positions.hydrated()` 70 ms, `net_worth()` 260 ms, the raw DB queries single-digit ms. Yet the
same `net_worth()` measured ~35 s on the live server under load: a **~134× blow-up**, and it grew
*super-linearly* with concurrency (a controlled burst of the real read flows measured **4.05×
worse than linear at 8 concurrent requests, 9.6× at 16**). Pure GIL serialization would be ~1×;
super-linear means a hot path was thrashing a shared resource.

`cProfile` named it: `PriceHistoryStore.latest_map()` and `prior_to_map()` — on the hot path
(`hydrated()` → `net_worth()`/`coverage()`, and `/api/prices`) — ran `SELECT price_source_id,
date, price ... ORDER BY (price_source_id, date)` over the **entire** `price_points` table
(~34k rows locally, ~210k live) and kept only the last row per instrument in Python, to return
~200 values. That is ~1000× more rows than the result. Alone it is "only" ~140 ms, but a tab-load
fires several of these at once, and each churns hundreds of thousands of Python-level `fetchone`
calls through the SQLAlchemy `QueuePool` (size 5 + 10 overflow) and the GIL. That churn — not the
database, not the language, not the single process — is what turned 260 ms into tens of seconds.

## Decision

Fix the **over-fetch**, not the language or the process model.

- `latest_map()`/`prior_to_map()` were rewritten as an **index-backed GROUP BY**: `MAX(date)` per
  instrument (using the composite PK `(price_source_id, date)`), joined back to one row each.
  Byte-identical result — `MAX(date)` per pid *is* "ascending date, last write wins", and
  `(pid, date)` is unique — verified equal to the old full scan across `latest_map` and
  `prior_to_map` at four day boundaries on a copy of the owner's data. This is a **read** returning the
  same prices, so no money math changed and it needs no migration.
- We deliberately did **not**: rewrite the money-bearing folds (`account_balance`, the
  cash/net-worth projections) as SQL aggregates — replicating the historical-rate lock in SQL is
  where correctness goes to die, and the profile showed they were never the bottleneck; add
  uvicorn workers / uvloop; or enlarge the connection pool. For one local user those are
  speculative infrastructure that trade correctness-surface and complexity for a burst load a
  single user does not generate.
- `coverage()` (~220 ms; loads all transactions several times) and `budget_aggregates()`
  (~140 ms; folds balances per account) have the same repeated-load smell but are left as-is:
  ~200 ms per request is not "so slow", and threading a single transaction load through the money
  projections risks the numbers for a modest gain. Recorded here so it is a conscious deferral,
  not an oversight.

## Consequences

- Measured on the owner's data copy (in-process, warm): `net_worth()` 224 → 56 ms (4.0×),
  `positions.hydrated()` 67 → 10 ms (6.6×), and an **8-request concurrent tab-load 5297 → 963 ms
  (5.5×)** — the super-linear contention ratio collapsed from 4.05 to **1.31**, i.e. back down to
  the plain-GIL floor. The remaining burst cost is now `coverage`+`budget` compute, not price
  scanning.
- `make perf-bench DB=<copy>` (`scripts/perf_bench.py`) is the durable measurement: warm median
  per flow + a concurrent-burst contention ratio. It is how this was found and how a regression
  would be caught. A regression test freezes the `MAX(date)`-per-instrument semantics
  (`tests/db/test_price_history_db.py::test_latest_and_prior_maps_pick_max_date_per_instrument`).
- **Standing guidance for the next "the app is slow".** Measure first (isolated vs. under
  concurrency — the gap *is* the diagnosis). Suspect a hot path fetching far more rows than it
  returns before suspecting the language or the process model. Reach for an in-database aggregate
  only for a **read** whose result is a small projection of a large table, never to move a
  money-bearing fold into SQL. Adding workers/uvloop/pool size is not the first lever for a
  single-user localhost app — and usually not the right one.
- `as_of_maps()` legitimately needs every point (net-worth history values held quantities at each
  historical date) and keeps its full scan.
