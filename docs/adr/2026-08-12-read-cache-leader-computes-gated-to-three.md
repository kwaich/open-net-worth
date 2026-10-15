---
title: Gate read_cache leader computes to 3 concurrent
status: Accepted
date: 2026-08-12
deciders: investigating Claude agent (perf root-cause session) + repo owner
---

# Gate read_cache leader computes to 3 concurrent

## Context

A page refresh fires ~18–28 aggregate GET endpoints at once. Each heavy read funnels through
`read_cache.cached`; on a cold epoch every one of them becomes a single-flight **leader** and
computes simultaneously in the threadpool (anyio's sync-endpoint pool defaults to 40 threads,
`asyncio.to_thread` adds its own executor). All of that work is pure-Python (payload JSON
parsing, ledger folds, FX math), so the GIL time-slices it.

Measured on real data (18 endpoints, in-process, cold cache): **serial 2.74s, concurrent
6.80s** — the concurrent burst is ~2.5× *slower* than running the same requests one by one, and
every individual request inflates to roughly the whole burst's wall (e.g. `net-worth/stats`
172ms serial → 6.8s in the burst). Against the live server the same free-for-all burst measured
8–9s — the owner's observed 10–20s page refreshes. A client-side sweep found concurrency 3
fastest (1.0s): a little parallelism overlaps SQLite I/O; more only thrashes context switches.

The epoch also churns every ~4s in production (automation event-outbox commits bump
`PRAGMA data_version`), so the cold-burst case is the *common* case, not a corner.

Owner constraint: no new caching — make the computation honest and fast.

## Decision

We will bound concurrent leader computes in `backend/db/read_cache.py` with a process-wide
`threading.BoundedSemaphore(3)`:

- Only single-flight **leaders** (and epoch-moved waiter fallbacks — the stampede path) pass
  through the gate; cache hits and followers are untouched.
- **Nested** cached calls made from inside a gated compute bypass the gate via a thread-local
  (a projection leader may call the hydration-context read; it must not self-deadlock).
- A leader that cannot acquire a slot within **8s computes ungated** — a liveness valve, so the
  gate can shape latency but never hard-wall a request.

## Consequences

### Positive

- Same 18-endpoint cold burst: in-process 6.80s → **2.58s**; live server 8–9s → **1.0–3.1s**.
  No computed value changes; freshness guarantees are exactly as before.
- The app stops DoS-ing itself when background polls overlap a tab load.

### Negative

- The cap is global and hand-tuned (3). On a future machine with a free-threaded Python or a
  much larger core count the right number may differ; it is not adaptive.
- A genuinely slow leader (e.g. one that performs a network fetch inside its compute) now also
  delays up to 2 other leaders' start. Network calls inside cached leaders were already a
  known smell (live price fetch) and are a separate fix.
- Rare interleaving (all 3 slots held by leaders following another key whose leader is still
  queued) resolves via the 8s valve rather than immediately.

### Follow-ups

- Stop the ~4s epoch churn (automation outbox stage/ack commits on the main DB) so warm-cache
  behaviour exists at all in production.
- Cut per-leader compute: `price_history_store.as_of_maps` does 39k–75k row-by-row
  `fetchone()` calls per request; `build_projection` computes 4 arg-variants per page load.
- Frontend fan-out hygiene: dedupe triple `/api/portfolio` fetches, paginate `/api/sessions`
  (409 kB per page load today).

## Alternatives considered

- **Shrink the anyio threadpool / default executor globally** — rejected: throttles *all* sync
  work including long network-bound connector calls behind aggregates; too blunt, and the
  follower-holds-a-slot deadlock shape gets worse, not better.
- **Per-endpoint dedicated executor for heavy routes** — rejected for now: touches ~15 router
  functions for the same effect the one-file gate achieves at the funnel every heavy read
  already passes through.
- **Do nothing and rely on per-endpoint optimisation** — rejected: even with every endpoint at
  50–350ms, 18 concurrent GIL-bound computes still multiply wall time; the burst measurement
  shows the execution model, not the endpoints, sets the floor.
- **A result cache with longer validity** — rejected: owner constraint (no caching as the fix),
  and the correctness-first epoch design is deliberate.

## Agent provenance

- **Decided by:** investigating Claude agent (systemic-slowness root-cause session)
- **Lane / branch:** `main` working tree (no lane; owner-invoked live investigation)
- **Alternatives weighed:** global threadpool shrink · per-route executors · endpoint-only optimisation · longer-lived cache
- **Driving artifacts:** measured burst harnesses (session scratchpad `instrumented_burst.py`, `eps.txt`), `PERF-REQ`/`loop_watch` log aggregation 2026-08-10→11, single-flight accounting table
- **Why this way (one line):** the GIL makes unbounded concurrent pure-Python leaders collectively slower than serial, so cap them at the measured optimum (3) at the one choke every heavy read already flows through.
