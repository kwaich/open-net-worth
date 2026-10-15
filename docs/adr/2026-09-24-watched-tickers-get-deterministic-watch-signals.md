---
title: Watched-not-held tickers get deterministic watch signals, a pulse strip and scan parity
status: Accepted
date: 2026-09-24
deciders: repo owner (asked for it) + Claude (implementation)
---

# 2026-09-24. Watched-not-held tickers get deterministic watch signals

## Context

Owner report: the Intel screen focuses on stocks you own and neglects the ones you only watch.
Traced, the gap was real at every layer, not just the UI:

- The brief (`intel_digest`), crash-watch, risk signals and the position bridge on story cards
  are all built from held positions. A watched ticker that moved 8% appeared nowhere.
- The scheduled scan (`intel_agent.run_intel_scan`) gave the LLM the portfolio and watchlist
  *topics* but never the watched tickers, fetched RSS only for holdings + topics, and queued
  watched tickers behind every holding for the shared 15-call search budget.
- The 5-minute RSS fast tier is held-assets only; watched tickers rode the 30-minute tier.
- `asset_detail`'s `next_earnings_at` reads Yahoo keys `fetch_quote_stats` never returns, so
  earnings was a dead field.

## Decision

1. **Watch signals are deterministic and free** (`services/watch_rules.py` pure rules,
   `watch_signals.py` emitter, `watch_pulse.py` read model). They read ONE batched Yahoo v7 quote
   (`prices.fetch_quote_snapshots`, 3-min cache) — day move (vs SPY), 52-week extremes, 50/200-day
   average crosses, volume vs 3-month average, earnings within 7 days, distance to the user's own
   price alert, and portfolio fit (sector overlap with what is held). No LLM, no search, no push.
2. **One consolidated story per watched ticker**, synthetic key `watch:<TICKER>`, written only
   through `apply_story_change`, carrying `watch: true` + `watch_kinds`. `STORY_KINDS` gates which
   findings raise a card; the rest (near a high, volume alone, "25% below high", near an alert)
   are pulse-only so a permanently beaten-down stock cannot pin a card to the feed forever.
3. **Lifecycle mirrors `risk_signals`**: idempotent upsert, auto-resolve when the trigger clears —
   with a 0.7× hysteresis and a 1-pt rewrite drift so a stock hovering at a threshold neither flaps
   resolved/revived nor rewrites its row (each write checkpoints). A dismissed story returns, unread,
   only when a NEW kind appears. A ticker that leaves the watchlist or becomes held resolves.
4. **Watched ≠ held.** `watched_items` excludes held tickers (they have crash-watch and the risk
   engine) and muted items. Thresholds are deliberately lower than crash-watch (5/6/10% vs 12/15/25%):
   this is "worth a look before you decide", not "your holding is cratering".
5. **Scan parity**: watched tickers appear in the LLM context as their own block, get RSS feeds in
   the scan, and hold a reserved search-budget floor (`_WATCH_BUDGET_FLOOR`, ≤ budget/3) claimed
   before holdings, with its own rotation cursor `intel_watch_cursor`.
6. **UI**: a Watchlist pulse strip under the brief (`GET /api/intel/watch-pulse`), a Portfolio /
   Watching feed scope (a partition, so Portfolio + Watching = All), a "Watching · not held" bridge
   on story cards, and day % + a story dot on the sidebar rows.

## Consequences

- The scheduler gains a `watch_signals` job (10 min) and the scan calls the same emitter.
- A moving-average CROSS is detected from one snapshot (prior close and price straddle the average),
  so no per-ticker state is stored.
- Crypto watch items whose stored value is not a Yahoo symbol (e.g. bare `BTC`) get no snapshot and
  so no watch signals — silently skipped, never guessed. Known gap.
- No push notifications for watch signals. Adding one is a policy decision (noise vs. missed move),
  deferred to the owner.

## Alternatives rejected

- **Extend crash-watch to watched tickers** — its thresholds and "owning it is enough" framing are
  wrong for a name the user has no position in, and it only detects down moves.
- **LLM-authored watch stories** — the deterministic rules already cover the events that matter and
  cost nothing per run; the LLM path (news-driven `asset` stories via the analyst) is unchanged and
  now simply sees the watched tickers.
- **One story per finding** — five findings on one ticker would be five cards; one consolidated card
  reads as "here is what changed on NVDA".

## Agent provenance

Claude (Sonnet 5), interactive session on `main`; scope chosen by the repo owner (all three slices,
pulse placed above the feed).
