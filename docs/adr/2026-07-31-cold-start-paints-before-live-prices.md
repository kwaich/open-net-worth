# 2026-07-31 — A reopened app shows its last known state; every read refreshes underneath

**Status:** Accepted
**Owner:** the maintainer (reported the symptom, approved all three fixes in-session)
**Touches:** `frontend/src/state/queryCache.ts`, `frontend/vite.config.ts`,
`frontend/src/hooks/usePortfolio.ts`, `frontend/src/hooks/serverState/keys.ts`,
`frontend/src/RouterShell.tsx`, `frontend/src/components/tabs/overview/Overview.tsx`,
`frontend/src/components/tabs/investments/InvestmentsTab.tsx`,
`frontend/src/components/tabs/investments/useInvestmentsBook.ts`,
`frontend/src/components/tabs/investments/viewCache.ts`, `frontend/src/hooks/useFire.ts`,
`frontend/src/hooks/useIntelDigest.ts`, `frontend/src/hooks/useMastheadSparkline.ts`

## Context

The owner reported that the installed mobile PWA "keeps refreshing and remounting whenever I
open the app," and attributed it to price refetching. That attribution was right, but the
mechanism was not the 60s poll — it was the FIRST load, in three compounding layers:

1. **Every reopen is a cold start.** An iOS standalone PWA (`"display":"standalone"`) is torn
   down rather than suspended when backgrounded: fresh JS context, fresh React tree, and an
   empty `state/queryCache.ts` (its entries live in a module-level `Map`, which dies with the
   context). `App.tsx` already documents this — it is why optimistic auth exists.
2. **The first portfolio read pays a provider round trip.** `GET /api/portfolio` defaults to
   `live=1`, which calls `portfolio_rows._refresh_prices` → `prices_service.fetch_live`
   (Yahoo/CoinGecko, 10-15s timeouts) BEFORE responding. `usePortfolio` had no persisted seed:
   only `wealthFX` was cached, so rows/totals started empty on every mount.
3. **The shell withheld the entire tab tree behind that one request.** `RouterShell` computed
   `initialLoading = loading && !hasLoaded` and rendered nothing but "Loading portfolio…" until
   it resolved, then mounted the whole subtree at once — so every child hook's own fetches
   started only after the slowest possible gate had opened.

The 60s poll calls `load(true)` (silent — it never touches `loading`), which is exactly why the
symptom appeared only on open and not mid-session. That is the tell that identified the gate
rather than the polling as the cause.

Fixing those three still left the app visibly reassembling itself on every open, and the owner
was explicit about the bar: *"I don't want any tab to show like it's reloading at all — it
should retain, show the old data."* The remaining cause was layer 1 generalized. The portfolio
was not special: **`state/queryCache`'s entries live in a module-level `Map`**, so ALL ~120 read
sites started empty on every reopen. `initialLoading` is defined as `data === undefined`, so
every widget skeleton, every "Loading accounts…", every sub-tab spinner fired at once. Several
hooks (`useIntelDigest`, the Investments `ViewCache`) even had their own module-level caches —
which dedupe within a session and die with it, so they solved tab re-visits and did nothing for
reopens.

## Decision

**1. The query cache persists across sessions.** `state/queryCache` mirrors itself to
localStorage (debounced, plus a flush on `pagehide`/`visibilitychange` — on iOS a backgrounded
app may never get another timer tick) and hydrates at module load, BEFORE React renders. A
reopened app paints its last known content immediately, the existing staleness check revalidates
in the background, and `publish()` swaps the fresh data in. This is stale-while-revalidate
across sessions, and it is why almost no new loading states were needed: `initialLoading` is
`data === undefined`, and hydrated data is defined. **No request is skipped** — hydration
decides what is on screen during the round trip, never whether the round trip happens.

Hooks that kept their own module-level cache were moved onto it, so they inherit the same
behaviour: the Investments `ViewCache` (6 sub-tab views) now delegates to the query cache under
a `viewcache:` namespace, and `useIntelDigest`, `useFire` and `useMastheadSparkline` became
ordinary cached reads. `useQueryCache` gained an `enabled` option for reads whose inputs are not
ready yet (FIRE before net worth is known), which returns cached data rather than a spinner.

**2. The portfolio's first read no longer waits on a price vendor.** A cold start goes through
three stages, each a strictly better answer than the last, none blocking a paint:

1. **The hydrated cache entry** — last session's response, applied synchronously in
   `usePortfolio`'s state initializers. Instant, possibly hours old, and never `is_live`.
2. **`GET /api/portfolio?live=0`** — last-known prices straight off disk, no provider call. Its
   result is also written through to the shared `/api/portfolio` cache entry, so the Accounts
   tab's holdings map and commitment funding values paint from it too instead of each waiting
   on the live call.
3. **`GET /api/portfolio`** — the real refresh, which replaces everything above in place.

Stages 2 and 3 run in **sequence, not in parallel**: two concurrent portfolio projections cost
double the CPU on a single-process GIL-bound backend, which is the contention the server-state
consolidation exists to prevent, and stage 2 is cheap enough that it barely delays stage 3.

**3. `RouterShell`'s whole-tree gate is gone.** Tabs mount immediately; `initialLoading` is
passed down only so a tab can tell "nothing yet" from "nothing at all".

## Consequences

- **`hasLoaded` changed meaning**, from "a request completed" to "there is content worth
  rendering" (a completed load OR the seed). It is the flag the shell uses to keep content
  mounted, and a seeded render has content.
- **An empty state over a pending read is now a lie about money, so it is banned.** Both tabs
  that consume portfolio rows offered a wrong answer when `rows=[]` meant "not yet": Overview
  said "Your wealth dashboard starts here" and offered to seed DEMO DATA; Investments said "No
  holdings yet". Both now render a loading state while a read is outstanding.
  `useInvestmentsBook` grew a `loading` field for the same reason — its `empty: true` was also
  true while `/api/portfolio/analytics` was merely in flight.
- **A hydrated value is a cache, never an input.** No figure is computed from one; every field
  is overwritten the moment a response lands, and a seeded render reports the entry's own fetch
  time as `lastUpdated` rather than "now" (`getCachedQueryUpdatedAt`). This preserves the
  historical-rate lock and the single-source rule (ADR `2026-07-31-single-source-net-worth`).
- **The mirror is keyed by build id** (`__BUILD_ID__`, stamped by vite's `define`). A persisted
  response is a SHAPE from the build that fetched it, and no generic validation can catch a
  shape change — so a deploy starts from an empty mirror (one slow open, then correct forever)
  rather than feeding yesterday's fields to today's components. Superseded keys are swept on
  hydration, so a deploy does not leak a couple of MB per build.
- **Most of the app's financial data now sits in localStorage** — same-origin, on the user's own
  device, on a self-hosted single-user app, and already true of the FX cache and dashboard
  layout. It is dropped by `clearQueryCache()`, i.e. on logout and on any 401.
- **Storage is capped**: ~2MB total, 300KB per entry, newest first. localStorage is a ~5MB
  per-origin budget shared with auth status, the FX cache, dashboard layouts and chat drafts,
  and `setItem` THROWS when full rather than evicting. Persisting the cache must never be the
  reason a user loses their layout; over budget, the oldest entries simply are not mirrored.
- **A background refresh is invisible by design.** `refreshing` drives no spinner anywhere (the
  one reader, LogViewer, guards on `!data`), so a revalidate updates figures in place.

## Alternatives rejected

- **Persisting only the portfolio** (where this change started) — the first attempt, and it
  fixed exactly one read while every other tab kept skeletoning on open. The bespoke
  `state/portfolioSnapshot.ts` it introduced was deleted in favour of the generic mirror.
- **Fetching `live=0` and `live=1` concurrently** — better time-to-live-data, but doubles
  startup CPU on the backend for a stage that already returns in ~50ms.
- **Keeping the gate and only adding the seed** — a first-ever open (no snapshot) would still
  block on the provider round trip, which is the exact experience being fixed.
- **Making `live=0` the endpoint default** — that would silently change what every other caller,
  including the AI tool surface, means by "the portfolio". The choice belongs to the caller that
  has a paint to protect, not to the resource.

## Evidence

`frontend/src/state/queryCachePersistence.test.ts` (a second "app open" hydrates the previous
session's data; a hydrated entry still refetches; the mirror is dropped on `clearQueryCache`;
a corrupt mirror starts empty; oversized entries are skipped; a different build's mirror is
never hydrated and is swept), `frontend/src/hooks/usePortfolio.coldStart.test.tsx` (paints from
`live=0` while the live read is still in flight; renders the seeded entry before ANY request
resolves; survives a failing `live=0`), and
`frontend/src/components/tabs/overview/Overview.loading.test.tsx` (loading state, not the
demo-data pitch, while the read is in flight).
