---
number: N/A
title: Make extended-hours (pre/post-market) price consistent across the app
status: Accepted
date: 2026-09-01
deciders: orchestrating agent (Claude) + repo owner
---

# Make extended-hours (pre/post-market) price consistent across the app

## Context

Earlier the same day, `backend/services/asset_detail.py`'s `read_asset_detail()` was found to
value a held position (market value, unrealized P&L) at the regular-session closed price even
while the SAME page's headline price correctly showed a live pre-market print — root cause was
one call site omitting `extended=` when threading a freshly-fetched live price into
`positions_service.hydrated()`. Fixing that one call site raised the obvious next question: how
many OTHER places have the same gap?

An audit (a forked agent with this session's full context) found the gap is structural, not a
handful of missed call sites:

- `resolve_price_quote(pid, prices, extended, prev_prices)` — the chokepoint that correctly
  prefers a live extended print over the regular price — only fires for a caller that has BOTH
  fetched live data itself AND threaded `extended=` through. Two callers did
  (`portfolio_rows.py`'s live path, and `asset_detail.py` now); ~25 others call
  `positions_service.hydrated()` bare (no arguments) — `income.py`, `dividend_calendar.py`,
  `cashflow.py`, `watchlist.py`, `rebalance.py`, `xray.py`, `ledger.py` (including
  `net_worth()`, the canonical net-worth fold), `fixed_income.py`, `options.py`,
  `portfolio_brief.py`, the `mentions/*` engine, `api/portfolio.py`'s `read_rows`, and every AI
  tool that reads holdings through `read_rows()`/`hydrated()` (`ai/tools/portfolio.py`'s
  `get_portfolio`, `ai/tools/screen.py`).
- A bare `hydrated()` call falls back to `positions.hydration_context()`'s `last_known`, read
  from `PriceHistoryStore.latest_map()` — the DAY-KEYED price-history table. That table's write
  path (`fetch_live`'s `record_history` block) only ever recorded the REGULAR price, never an
  extended print, so `last_known` was structurally incapable of reflecting one, regardless of
  how recently a live extended quote had been resolved anywhere else in the process.
- `backend/monitoring/price_monitor.py::run_price_check` — the price-alert engine — fetches
  live data (which includes `extended`) but only read `prices_result["prices"]` when checking
  alert/crash thresholds. A "notify if X drops below $Y" alert would not fire on a live
  pre/post-market cross, only once regular trading later confirmed the same move — a genuinely
  missed notification, not just a display lag.
- `ai/tools/prices.py`'s `get_live_prices` tool and `GET /api/prices` both return
  `fetch_live()`'s raw `{prices, extended, prev_prices}` split straight to the caller with no
  single resolved "current price" field — the same shape of gap that caused the original bug,
  now facing an LLM/API consumer instead of a service.

## Decision

We will make "the current price" consistent everywhere by fixing the gap at its root instead of
patching each call site:

1. **A new, separate in-memory cache** (`services.prices._live_price_cache` /
   `latest_live_prices()`) holds the EXTENDED-preferred resolved price per instrument, written
   unconditionally on every real `fetch_live()` round-trip (regardless of `record_history`).
   `positions.hydration_context()`'s `last_known` now overlays this cache on top of
   `PriceHistoryStore.latest_map()` — a bare `hydrated()` call is extended-aware whenever
   something has fetched live data recently (in practice: the ~60s scheduled price-check
   sweep, which polls essentially every held ticker while its market is open), and falls back
   to the stored day-keyed price exactly as before when nothing has.
2. `price_monitor.py::run_price_check` now builds `current_prices` (both the alert lane and the
   crash-watch lane) preferring `extended[ticker]` over `prices[ticker]`, matching
   `resolve_price_quote`'s own preference rule.
3. `fetch_live()`'s return dict gains a `resolved` field — one number per ticker,
   extended-preferred — alongside the existing raw `prices`/`extended` split. `get_live_prices`
   and `GET /api/prices` inherit it for free (both just return `fetch_live()`'s dict); the AI
   tool's description now tells the model to prefer `resolved`.
4. `ledger.net_worth()` and every other bare-`hydrated()` caller are UNCHANGED in how they call
   `hydrated()` — the owner explicitly decided net worth should keep reading "last known price"
   as a mechanism (a stable, deliberate choice, not an oversight). #1 makes that mechanism
   itself extended-aware, so this requirement is satisfied without touching `ledger.py` at all.

## Consequences

### Positive
- One fix (`positions.hydration_context()`'s cache overlay) makes ~25 call sites — including
  net worth, every AI tool that reads holdings, and every service listed above — extended-aware
  at once, instead of a 25-site whack-a-mole that's easy to regress (as the original bug
  itself was — a caller doing the multi-step "fetch live, thread into hydrated()" dance simply
  forgetting `extended=`).
- Price alerts now fire on the same live price the asset-detail page shows the user.
- An LLM/API consumer of raw `fetch_live()` output no longer has to know to prefer `extended`
  over `prices` itself — `resolved` says so directly.
- The day-keyed `price_history` table (and everything that depends on its "one point per
  trading day = that day's regular close" invariant — `prior_to_map()`, the day-P&L baseline
  used across the app) is completely untouched — no risk of an extended print corrupting a
  day-over-day comparison.

### Negative
- A second, non-persisted cache to reason about (`_live_price_cache`, in-memory, resets on
  restart) — a fresh process serves last-known-only prices until the first live fetch
  repopulates it (typically within the first ~60s scheduled sweep). Documented in the cache's
  own module docstring; test-isolated via a new autouse `tests/conftest.py` fixture
  (`_reset_live_price_cache`) so it can't leak state across the test suite.
- `hydration_context()` now does one extra dict-merge per call — negligible (in-memory, no I/O)
  next to the store reads already happening there.

### Follow-ups
- None currently planned. If this proves valuable, `_live_price_cache` could later be
  persisted (e.g. Redis, or a lightweight table) to survive a restart without the brief
  cold-cache window — not needed today given the ~60s sweep repopulates it quickly.

## Alternatives considered

- **Write the extended-preferred price directly into `PriceHistoryStore`'s existing day-keyed
  write path** — rejected. That table's "one point per trading day" semantics is the day-P&L
  baseline every `prior_to_map()` consumer relies on meaning "that day's REGULAR close". A late
  evening `record_history` write during POSTPOST/PREPRE would overwrite today's row with a
  thin-volume extended print, and the NEXT day's `prior_to_map()` would then anchor day-change
  to that instead of the real close — corrupting a different, already-correct invariant to fix
  this one. The separate, non-day-keyed cache avoids this entirely.
- **Fix each of the ~25 bare-`hydrated()` call sites individually** (thread `prices=`/
  `extended=` through each one) — rejected as the primary fix. Correct in principle, but it's
  exactly the failure mode that caused the original bug (one caller forgetting the extra
  kwarg), doesn't scale, and most of those callers deliberately avoid an extra live-fetch round
  trip per request (several have their own comments saying so) — the cache overlay gets them
  fresher data without adding a network call to any of them.
- **Bring `ledger.net_worth()` onto the live-fetch path** (matching `portfolio_rows.py`'s
  `live=True` mode) — rejected per explicit owner decision: net worth should keep using
  "last known price" as its mechanism. Satisfied instead by making that mechanism itself
  extended-aware (Decision #1), which was the owner's own resolution once the tradeoff was
  named.

## Agent provenance

- **Decided by:** orchestrating Claude agent (interactive session), confirmed by repo owner
  ("fix all of them, e2e please" / "net worth must use last known price too")
- **Lane / branch:** `fix/income-source-labels-and-colors` (interactive session, not a
  lane/worktree build)
- **Alternatives weighed:** write extended prices into the day-keyed price_history table vs. a
  separate live-price cache vs. fixing each bare-`hydrated()` call site individually vs.
  bringing `net_worth()` onto the live-fetch path
- **Driving artifacts:** the `asset_detail.py` position-value bug fixed earlier the same
  session (PLTR, owner-reproduced live), the audit fork's findings report, the price-alert gap
  found in `price_monitor.py::run_price_check`
- **Why this way (one line):** one small, additive cache fixes the ~25-site structural gap at
  its root without touching the day-keyed table's own correctness invariant, and satisfies the
  owner's explicit "net worth keeps using last-known" requirement for free.
