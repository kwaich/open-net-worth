---
title: Concentration, today's move and the benchmark have one authority across Investments and Intel
status: Accepted
date: 2026-09-25
deciders: repo owner (asked to check Intel/Investments reuse and conflicts) + Claude (implementation)
---

# 2026-09-25. Concentration, today's move and the benchmark have one authority

## Context

Splitting Intel into a Holdings tab puts it beside Investments, so any figure both screens show
must match. A trace found four implementations that disagreed:

- **Single-name concentration.**
  - `investments_book._concentration` (Risk card): merges by name, uses the invested book
    (`MARKET_CLASSES`) as the denominator, and is breached at or above the Risk-card guideline
    (`profile.risk_concentration_guideline`, default 20%).
  - `portfolio_brief._concentration_signal` (Portfolio brief): takes the largest single ROW, so
    the same holding at two brokers never merged.
  - `risk_signals.compute_risk` (Intel risk story): uses a tolerance table (15% moderate) and
    every priced row including cash, with values in a `{instrument_id: value}` dict
    comprehension. That keeps only the LAST account's leg, so 12% + 12% at two brokers read as
    12%. The class, sector and FX folds then added that one leg once per row, and the drawdown
    check compared one leg's value to another leg's cost.
- **Today's move.**
  - The masthead divides by the book's value at the prior close.
  - The Portfolio brief and the Intel brief divide by today's value.
  - The Intel brief also counted cash in the denominator.

  That gave three different "+x% today" figures for the same day.
- **Benchmark.** Performance lets the user pick SPY/VWRA/QQQ/ACWI/BTC (localStorage only).
  The Intel digest always took the first instrument from a fixed list, which is SPY, and the
  watch pulse hard-coded SPY.

## Decision

`services/investments_book.py` is the authority. The others call it:

- `is_invested(row)` and `project_invested(rows, base, rates)`: the invested book (instrument-backed
  `MARKET_CLASSES` rows, never unvested grants), valued by `portfolio_rows.value_row`.
- `concentration_by_name(rows, grand, guideline)`: merges by name. `hot` means at or above the
  guideline. It is used by the Risk card, the Portfolio brief and the risk story.
- `concentration_guideline(profile)`: the Risk-card guideline. The **single-name risk story no
  longer follows `risk_tolerance`**. The other risk checks (class, sector, FX, region,
  drawdown) keep the tolerance table, but move to the invested denominator and count an
  instrument held at several accounts once.
- `today_move(projected)`: the percent is measured against the prior close. It is used by the
  masthead, the Portfolio brief's Today signal and the Intel digest's `today`, which stays a
  fraction on its own wire.
- **Benchmark.** A profile field `preferred_benchmark` (a `BENCHMARK_ROSTER` key, default SPY,
  validated by the API, read through `risk_metrics.preferred_benchmark`). The Performance picker
  saves it on an explicit change. The Intel digest resolves it first, and the watch/holdings
  pulse quotes its Yahoo listing (`VWRA.L`, and `BTC-USD` for BTC).

Tests: `tests/test_book_figures_parity.py`, which asserts the same verdict and the same number
through every caller, and `tests/test_risk_signals.py`, covering the multi-account and cash
denominator regressions and the guideline test.

## Consequences

- A user on the "aggressive" tolerance no longer gets a single-name story at 25%. It fires at
  their Risk-card guideline, the same value the Risk card shows as hot.
- The Intel "Today" percent moves slightly, from dividing by today's value to dividing by the
  prior close. `tests/test_intel_digest.py::test_today_pl_math` was updated on purpose.
- Callers that hold hydrated rows value them with `value_row` at cached prices. The Investments
  masthead values live-refreshed rows. So the formula is the same, but the price snapshot can
  differ by one refresh. This is known and accepted.
- `macro_exposure` still keeps its own `_row_base_value` and denominator. It is not on a screen
  beside Investments, and is left for a later pass.

## Agent provenance

Claude (Opus 5.5), interactive session on `main`, as Part 1 of the plan "Equity investing: Intel
tabs, one source of truth with Investments, and a backlog".
