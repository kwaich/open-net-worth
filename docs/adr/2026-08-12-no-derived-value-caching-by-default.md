---
title: No derived-value caching by default — optimize first, redesign caching deliberately
status: Accepted
date: 2026-08-12
deciders: repo owner (explicit call) + investigating Claude agent
---

# No derived-value caching by default — optimize first, redesign caching deliberately

## Context

Two caching layers existed as band-aids for slow endpoints, and both bit back on 2026-08-11/12:

- Backend `read_cache` memoisation ran permanently cold (its global `data_version` epoch was
  invalidated every ~4s by automation-outbox bookkeeping commits — 21 invalidations/90s
  measured) while still charging an 80–260ms `deepcopy` per hit.
- The frontend `queryCache`'s default `staleTime` of 30s served a **stale net worth across tab
  switches after the owner edited an account's starting balance** — a stale money number, the
  one thing this codebase's invariants exist to prevent. Every explicit `staleTime` in the
  codebase was already `0`: call sites had been hand-patching the default away one staleness
  bug at a time.

Meanwhile the endpoints themselves had become fast (40–230ms serial with no caching at all;
worst-case fully-cold 18-endpoint burst 3.6s), so the caches' complexity was buying staleness
risk, not speed. Owner's directive, verbatim intent: disable caching, optimize the endpoints
fully with the best UX, then redesign a proper caching layer deliberately.

## Decision

We will serve **no derived money number from a store by default**:

- `read_cache` memoisation is OFF unless `ONW_READ_CACHE=1` (`_memo_enabled()`); every read
  computes. The `_gated_compute` concurrency gate is explicitly NOT part of this — it shapes
  concurrency and caches nothing, and stays on.
- The frontend `queryCache` default `staleTime` is **0** (both `useQueryCache` and
  `fetchCachedQuery`): cached data may paint only while its own refetch is in flight —
  stale-while-revalidate with mandatory revalidation, never a freshness window.
- External market data is exempt (see the sibling provider-fetch ADR): a price is provider
  data on a refresh cadence, not a derived number.

## Consequences

### Positive
- Staleness bugs of this class become structurally impossible; mutation call sites no longer
  need hand-maintained invalidation lists to be correct.
- Performance work now measures the true cost of every read, so optimization targets are
  honest.

### Negative
- Every tab switch refetches (~100–300ms/read) and every request recomputes; the fully-cold
  burst costs ~3.6s of compute that a working cache could partly avoid. Accepted while data
  is small.
- `ONW_READ_CACHE=1` exists as a footgun if enabled casually without the redesign.

### Follow-ups
- Endpoint hygiene batch, then composite per-page reads (`docs/PERFORMANCE.md` backlog 1–2).
- The deliberate cache redesign, only with evidence: per-table epochs (outbox chatter must
  not invalidate money reads), no deepcopy on hit, cross-variant dedup.

## Alternatives considered

- **Fix the frontend invalidation lists** — rejected: hand-maintained per-mutation lists
  regrow staleness bugs forever; the codebase's own history (every override already 0) is the
  proof.
- **Fix the epoch churn and keep memoisation on** — rejected for now: fixes THIS invalidator,
  keeps the global-epoch design that makes the next one; redesign belongs after optimization.
- **Keep 30s staleTime but invalidate on every mutation route** — rejected: same list-upkeep
  trap, and money numbers deserve zero freshness window.

## Agent provenance

- **Decided by:** repo owner (explicit directive), implemented by investigating Claude agent
- **Lane / branch:** `main` working tree (live investigation session)
- **Alternatives weighed:** invalidation-list repair · epoch-churn fix + keep memo · staleTime with route invalidation
- **Driving artifacts:** the starting-balance staleness incident (2026-08-12), epoch-churn measurement, cache-off burst measurement (3.59s), `docs/PERFORMANCE.md`
- **Why this way (one line):** the endpoints are fast enough to be honest, so no derived money number is worth any staleness window.
