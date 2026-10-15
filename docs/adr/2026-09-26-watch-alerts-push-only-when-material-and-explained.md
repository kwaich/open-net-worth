---
title: Watch alerts push only when material and explained
status: Accepted
date: 2026-09-26
deciders: repo owner (chose "all three tiers" and "material + explained only" for push) + Claude (implementation)
---

# 2026-09-26. Watch alerts push only when material and explained

## Context

The owner asked whether the Watching pulse was too surface level to give an educated alert. It
was:
- It read one quote snapshot and applied fixed rules (≥ 5% day move, near a 52-week extreme, and
  so on).
- It never read why a stock moved, though `monitoring/price_movements.py` already writes a
  sourced "why it moved" note for watched names.
- It couldn't tell an unusual move from an ordinary one for that stock, or company news from the
  market.
- It knew nothing about why the user watches the name.
- It misread split days as crashes.

Backlog INVCOV-23 had left the push question open (noise versus a missed move).

## Decision

Three tiers. Each is usable alone and feeds the next.

1. **Sharper deterministic detectors, free.**
   - These live in `watch_stats`, `watch_history_rules` and `watch_market`, read through
     `watch_context.evaluate_item`, the ONE place the pulse, the stories and the explained alerts
     get a ticker's findings.
   - A move is scored against the stock's own 60-day daily standard deviation ("11× its usual
     daily move"). It is labelled company-specific, market or sector via β against the user's
     `preferred_benchmark` and the sector's SPDR ETF.
   - Multi-day patterns: drawdown from the 20-day high, streaks, the month against the
     benchmark, and a 200-day cross kept for 5 sessions.
   - Volume is judged on pace for the time of day.
   - A move matching a split ratio on ordinary volume is named "possible split", and every
     price-based rule pauses.
   - History is local `price_points`. Watched instruments are topped up twice a day by
     `prices.backfill_watched_instruments` (1Y when thin, 1M otherwise).
   - A calm stock's story resolves on the same yardstick that raised it (z, not a fixed 5%), so
     it doesn't flap.
2. **Your reasons, as rules.**
   - A watch item carries an optional `intent` (thesis ≤ 280 characters, a `buy_below` /
     `buy_above` zone, `fair_value`). It is validated at the `WatchlistStore` write choke, needs
     no migration (the `watchlists` row is a JSON payload), and is exposed on the REST PATCH and
     the `update_watchlist_items` tool.
   - Rules: entered / in the buy zone; crossed below / below / above fair value; "Earnings in N
     days — check your thesis".
3. **The explained alert.** Implemented in `monitoring/watch_analyst.py`.
   - **Material triggers** (`watch_rules.material_triggers`) — nothing else is material:
     - a company-specific move ≥ 2.5 usual moves;
     - entering the buy zone;
     - crossing 10% below fair value;
     - a 52-week breakout or breakdown on volume on pace ≥ 2×.
   - **The model is handed** a closed set of facts and the note's sourced paragraph and sources,
     and returns: what happened, why (citing source ids), what it means for you, and worth
     weighing.
   - **`check_result` rejects** (with one bounded reask):
     - any number not in the facts or the sourced text (a digit glued to letters, such as
       "MI400" or "Q3", is a name, not a figure);
     - any verdict ("you should", "buy now", …).

**Push policy.** Only an explained alert pushes: material, and with a sourced "why". It goes
through the same Notifier + `push_retry` path as price alerts, to `alert_channels`. Every other
watch finding stays in-app (pulse chips and the feed card).

Amended by ADR 2026-09-27-watch-story-reads-and-opinion-level: explained alerts can be turned
off, their opinion level (information / lean / suggest) is a user setting, and every other live
watch story gets an in-app read that never pushes.

**Guards:**
- no sourced "why" → no alert, and the story stays deterministic;
- one alert per ticker per trigger per session (`trigger_key`);
- `MAX_EXPLAINED_PER_DAY = 8`;
- skipped when no LLM provider is registered;
- muted tickers never reach it;
- a revived story drops the previous episode's explanation.

## Consequences

- A watchlist of 30 names costs at most 8 short model calls a day, and usually far fewer, since
  most days have no material trigger with a sourced reason.
- A watched ticker without an instrument (or with a manual price) has no history, so its rules
  fall back to the fixed thresholds and it can't raise a company-move trigger. Buy-zone and
  fair-value triggers still work.
- A split day stops producing false crash-style watch flags on the Intel side (the INVCOV-1
  Intel note). The data fix for lots is still INVCOV-1.
- Advice stays out by construction: the prompt forbids it, the check rejects it, and the UI
  labels the last section "Worth weighing".

## Alternatives rejected

- **Push every trigger:** too noisy, since 2σ days are routine across a large watchlist.
- **Never push:** misses the one alert a user would want on their phone, "it just entered your
  buy zone, and here's why it moved".
- **Let the model find the "why" itself (search):** duplicates the price-movement pipeline, and
  its sources wouldn't be server-built.

## Agent provenance

Claude (Opus 5.5), interactive session on `main`. The plan "From 'something moved' to an
educated watch alert" was approved by the owner.
