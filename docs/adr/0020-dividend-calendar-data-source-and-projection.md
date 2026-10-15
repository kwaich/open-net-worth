---
number: 0020
title: Dividend calendar — Yahoo-declared vs history-projected, no record date
status: Accepted
date: 2026-06-21
deciders: solution-architect (Claude) + repo owner (the repo owner)
---

# 0020. Dividend calendar — Yahoo-declared vs history-projected, no record date

## Context

The Income tab is gaining an **Upcoming payments** calendar (ex-date / pay-date / estimated amount per
holding) plus a corrected forward-yield metric. The only price/dividend source wired today is Yahoo
(`yfinance`), which we already call for names/sectors/`dividend_rate_annual`. A live spike (2026-06-21,
`yfinance==1.2.0`) against the owner's holdings established what Yahoo will and won't give us:

- **Structured forward dates are unreliable.** ETFs (QQQ/VOO/VXUS) **404** on the `quoteSummary`
  module that carries `exDividendDate` / `dividendDate` / `calendar` (VXUS even returned a 2016 date).
  Equities return them, but only the **most recent** event unless the company has *declared* the next
  one (AAPL showed a past ex-date; MSFT — having declared — showed a real future ex 2026-08-20, pay
  2026-09-10).
- **Dividend *history* is solid.** `Ticker.dividends` returned clean, current quarterly series for
  every payer; projecting last-ex + inferred cadence reproduced the design mock's dates closely.
- **No record date exists** anywhere in Yahoo's payload.
- **Non-payers** (GLD, a gold trust) carry zero yield / empty history.

The full design is in `.claude/rfcs/dividend-calendar-income-redesign.md`; this ADR records the
load-bearing data decisions.

## Decision

1. **Two-tier ex/pay dating, with explicit provenance.**
   - **scheduled** — when Yahoo's `exDividendDate` is in the *future* (a declared dividend): use
     Yahoo's exact ex-date + pay-date.
   - **estimated** — otherwise: **project** the next ex-date at *read time* as `last_ex_date + cadence`
     rolled past today, with `pay_date = ex_date + 14d` (constant; Yahoo history gives ex-dates only).
   Each calendar row is tagged `status: "scheduled" | "estimated"` and the UI colours them
   (green/grey) so we never imply precision we lack.

2. **Omit record date entirely.** It is not in Yahoo, and is definitionally `ex-date + 1 business day`
   (T+1). The **ex-date is the eligibility gate**; record date adds no user-actionable information.

3. **Cache Yahoo facts on the instrument; compute projection at read time.** The dates ride **free**
   on the `.info` call we already make; `Ticker.dividends` adds one chart request per symbol on a
   **daily** scheduled refresh (`refresh_dividend_meta`). Stored: `next_ex_date`/`next_pay_date`
   (future-only), `last_ex_date`, `last_dividend_amount`, `dividend_frequency`. The *projected* next
   date is recomputed per request so it never goes stale relative to "today". Non-payers keep null
   meta and are excluded from the calendar.

4. **Forward yield is reported on market value (added additively).** `forecast_income` gains
   `market_value_base` + `forward_yield_pct` (= forward income ÷ current market value, the standard
   dividend yield). The misleading cost-basis `implied_forward_yield_pct` is retained but no longer
   the headline; the new UI's "Forward yield" card uses `forward_yield_pct`.

## Consequences

### Positive
- An accurate dated payment calendar across mixed holdings without fabricating data.
- Honest UI: "scheduled" only when truly declared; everything else visibly "estimated".
- Near-zero added Yahoo load (dates free; one cacheable history request/symbol/day at personal scale).
- The two header yields finally measure comparable things (yield-on-cost vs yield-on-market-value).

### Negative
- Most ETF-heavy portfolios will show **mostly grey/estimated** rows — green coverage depends on the
  holding mix (declared large-cap equities). This is reality, not a defect.
- Pay dates on the estimated path are a flat `+14d` approximation (history exposes ex-dates only).
- Adds 6 cached fields to `instruments.json` (portable, checkpoint-covered, nullable — no migration).
- Yahoo `quoteSummary` 404s for ETFs are tolerated (fall to projection); a Yahoo schema change would
  silently reduce green coverage (degrades gracefully to estimated).

### Follow-ups
- A non-Yahoo source (Nasdaq dividend calendar / issuer / paid feed) if true "announced" coverage for
  ETFs becomes a requirement.
- Optionally learn a per-instrument ex→pay offset if a reliable historical pay-date source appears.
- Reconcile recorded income events against Yahoo's historical `dividends` series (auto-backfill of
  "received" history) — separate effort.

## Alternatives considered

- **Trust Yahoo's forward fields directly.** Rejected: 404 for ETFs, stale for undeclared equities —
  the spike proved it would be wrong for most holdings.
- **Show only "announced" (scheduled) rows, drop projection.** Rejected: would empty the calendar for
  ETF-heavy books, the common case here.
- **Include record date.** Rejected: unavailable from Yahoo and adds no actionable signal over ex-date.
- **Change `implied_forward_yield_pct` in place to market-value basis.** Rejected in favour of an
  additive `forward_yield_pct` to avoid silently altering an existing metric's meaning.
- **Fetch on page load.** Rejected: rate-limit exposure on the hot path; cache + daily refresh instead.

## Agent provenance

- **Decided by:** solution-architect (Claude), commissioned by the repo owner (AFK build).
- **Lane / branch:** `feature/portfolio-performance-returns` (income feature is disjoint from the
  parallel returns work; new files + income-only edits, `types/portfolio.ts` untouched).
- **Driving artifacts:** live `yfinance` spike (QQQ/VOO/VXUS/AAPL/MSFT/GLD, 2026-06-21);
  `.claude/rfcs/dividend-calendar-income-redesign.md`; recon of `prices.py`/`scheduler.py`/
  `instrument_store.py`/`positions.hydrated()`.
- **Why this way (one line):** Yahoo gives reliable dividend *history* but flaky forward *dates*, so
  use declared dates when present and project from history otherwise — labelled honestly — and never
  invent a record date or an announced amount we don't have.
