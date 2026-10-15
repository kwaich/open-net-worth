---
title: Provider fetches never block interactive requests
status: Accepted
date: 2026-08-12
deciders: repo owner ("keep") + investigating Claude agent
---

# Provider fetches never block interactive requests

## Context

Live price fetches (Yahoo/CoinGecko) ran inside request handlers: `fetch_live` kept a 60s
in-process cache, and on TTL lapse the next request performed the full provider round-trip
while holding the per-key lock every concurrent money read queues behind. Free providers
throttle progressively as a day's usage accumulates — measured on 2026-08-12 00:09: one
**35.3s** provider fetch produced a 35.7s `/api/portfolio` and ~30s stalls on unrelated
endpoints. This was the app's "fine at noon, terrible at night" signature: all tabs, both
devices, uncorrelated with any code change. 85 provider-failure log lines (429s/timeouts) in
that hour alone.

## Decision

We will never let an interactive request wait on a market-data provider:

- `fetch_live(..., allow_stale=True)` — interactive callers (portfolio rows, asset detail,
  the `/api/prices` route) get the last-fetched result immediately when the 60s TTL has
  lapsed, and ONE background thread per cache key refreshes (`_refresh_in_background`,
  non-blocking lock acquire = in-flight dedup).
- Backstops: no cached entry at all, or an entry older than 15 min (`_STALE_HARD_LIMIT`),
  still blocks and fetches — a fresh boot gets real data, and staleness is hard-bounded.
- Background consumers (the 60s price_check scheduler, price monitor, the AI tool) keep
  blocking-fresh semantics unchanged; the scheduler is what keeps the cache ≤ ~60s old in
  steady state.

This is a freshness policy for EXTERNAL provider data, not derived-value caching — it is the
explicit exemption in ADR `2026-08-12-no-derived-value-caching-by-default`.

## Consequences

### Positive
- The 30–35s all-tab stalls are structurally gone; provider throttling degrades quote age,
  never tab latency.
- Provider load drops (background refreshes dedup in flight).

### Negative
- Under heavy throttling the UI can show quotes minutes old while the header still says
  "Prices: live" — an honesty gap until quote age is surfaced.

### Follow-ups
- Pass the quote's fetched-at age through to the UI ("as of 00:14") instead of a bare "live".
- Backlog #3 (`docs/PERFORMANCE.md`): move price polling into a worker process entirely.

## Alternatives considered

- **Shorter provider timeouts** — rejected: still blocks tabs for the timeout, and throttled
  providers then fail more, serving nothing.
- **Only the scheduler ever fetches** — rejected for now: request-path keys differ from the
  scheduler's set (holdings+FX vs alert tickers), so a key the scheduler never warms would
  serve unbounded-stale data; the 15-min backstop keeps a bound today. Revisit under
  backlog #3.
- **Do nothing (cache TTL already existed)** — rejected: the TTL-lapse fetch inside the lock
  IS the measured 35s stall.

## Agent provenance

- **Decided by:** investigating Claude agent, ratified by owner mid-session ("for that, keeps")
- **Lane / branch:** `main` working tree (live investigation session)
- **Alternatives weighed:** shorter timeouts · scheduler-only fetching · status quo
- **Driving artifacts:** 00:09 correlation (35.3s fetch = 35.7s /api/portfolio), hourly price-fetch degradation table, `docs/PERFORMANCE.md` layer 4
- **Why this way (one line):** a quote a minute old is market reality; a tab frozen 35s is a bug — pick which one the user pays.
