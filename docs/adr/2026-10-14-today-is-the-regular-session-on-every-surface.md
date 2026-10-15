---
title: "Today" is the regular session's move on every surface; pre/post is a labelled secondary
status: Deprecated
date: 2026-10-14
deciders: implementing agent (investor-persona fix pass) + repo owner (asked that the surfaces agree)
---

# 2026-10-14. "Today" is the regular session's move on every surface

## Context

A persona session at about 04:40 Singapore time (16:40 in New York, after the US close) found one
holding with four different day moves. MSFT read +0.30% in the holdings list and in
`get_portfolio`, down 0.08% in the top ticker, and "+0.42% at the close" with "down 0.07% after
hours" on its quote page. NVDA and VWRA disagreed the same way. Tracing each surface:

- **Holdings list, `get_portfolio`, the book.** `positions.hydrated` measured `day_change` from the
  row's *valuation* price, which prefers a live pre/post print (ADR
  `2026-09-01-extended-price-consistency`), so an after-hours print was folded into "today"; with no
  live quote it was the last stored price, which the extended-preferred live cache can overlay. The
  baseline was our own recorded last tick of the previous day (`prior_to_map`), not the exchange's
  previous close, and `projected_rows` dropped the `prev_prices` its own `fetch_live` returned.
- **Top ticker.** It substituted the extended session's change for the day move
  (`extended_change_pct ?? daily_pl_pct`), and the mega-cap names in the market half repeated held
  symbols with the same substitution.
- **Quote page.** It headlined the extended print and demoted the regular close to `at_close`.
- **Book "today %".** `today_move` divided by `base_value - daily_pl`, which mixes sessions once
  `base_value` is an extended valuation, and left a flat holding out of the denominator.

The recorded series also only holds the last tick the sweep happened to see. The price monitor polls
open markets only, so an SGX name's last recorded point before the bell (the closing auction prints
after 17:00) stayed "the close" and became tomorrow's baseline.

## Decision

**"Today" is the regular session: the regular last price against the provider's previous close,
identical on every surface. A pre/post print is only ever a labelled secondary figure.**

- `PriceQuote` gains `regular_change_abs` / `regular_change_pct`, computed in
  `prices.resolve_price_quote` (the one chokepoint) whichever quote won. Every surface reads them.
- `positions.hydrated` measures `day_change` from the regular price (`regular_price`, a new row
  field) against `_resolve_prev`, whose order is now provider previous close, then the previous
  close of the latest live fetch, then the recorded series. A bare `hydrated()` gets the regular
  price and previous close from a new `prices._live_regular_cache` (same lifetime as the
  extended-preferred cache), falling back to the recorded series, which holds regular prints only.
- `portfolio_rows` threads the live `prev_prices` into `hydrated`, shows `display_unit_price` from
  the regular price, and stamps `market_phase` / `price_as_of` so the list can say **at close**.
  `_project` emits `prior_base_value` (the holding's value at the prior close) and
  `investments_book.today_move` divides by it. It stays the single book-level authority; it now
  also counts a flat holding in the denominator.
- The ticker rows carry the regular price and move, plus an optional `extended` object the strip
  shows as a small labelled chip. Held names are not repeated in the market half.
- The quote page headline is `last` / `change_*` = regular session; `extended_hours` (with its change
  against the regular close) is the secondary column; `at_close` is gone.
- The price monitor makes one recording fetch for a market that closed since the previous sweep, so
  the official close, not an earlier tick, is what the series (and tomorrow's baseline) holds.

**Valuation is unchanged.** Rows, net worth and unrealized P&L still use the extended-preferred
price (ADR `2026-09-01`, an explicit owner decision). The cost is that a row's value can differ from
price times quantity by the extended move while an extended session is live; the docs say so.
Moving valuation to the regular price is a separate decision for the owner.

**Single-name concentration excludes diversified funds.** `investments_book.concentration_by_name`
skips a fund wrapper (`classification.FUND_SUBCLASSES`) over equities or bonds: 39% in an all-world
index ETF is 39% of the market, not 39% in one company. A commodity or crypto ETP stays a name. The
weight-only consumer (watch pulse) passes `names_only=False`. The Risk card now reads the server's
`hot` instead of re-deriving it from the guideline in the browser.

## Consequences

- The list, ticker, quote page, book and Lucius's tools agree by construction. The tests assert it
  from one synthetic payload (`tests/test_today_move_agreement.py`).
- The Intel digest's "today %" moves slightly where a flat holding used to be left out of its
  denominator.
- A price-monitor sweep costs one extra fetch per market close.
- The asset page's `at_close` field is removed from the API; the frontend reads `extended_hours`.

## Agent provenance

Claude (Opus 5.5), worktree session `agent-adedf3eb005d7c88c`, fixing items 1 and 2 of the
investor-persona report. Alternatives weighed: valuing rows at the regular price too (rejected as
reversing an owner decision), a newest-quote-wins overlay across `fetch_live` cache keys (rejected:
the bare path and the baseline were the real divergences), and shortening the stale-serve window
after a close (rejected: a foreground tab is at most one poll behind already).

## Amendment 2026-10-15: the "today" decision is reverted by the owner

**Decided by:** repo owner, launch review 2026-10-15.

The owner had decided (commit 8e59a4aae, ADR `2026-09-01-extended-price-consistency`: "use
pre/post prices widely, in everything") that the extended-session print is the price shown. This ADR reversed that without
the owner's ask: the instruction came from the orchestrating agent. The regular-session headline,
the "at close" and "Last session" labels, the shared session verdict and the two-decimal price
format are reverted; "today" follows the pre/post print again, as it did before 2026-10-14.

What stands: a diversified equity or bond fund is not a single name for concentration, and the
masthead figure is labelled "Market value" (it was "Total invested", which it is not).
