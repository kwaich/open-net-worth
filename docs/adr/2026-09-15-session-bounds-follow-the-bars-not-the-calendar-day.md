---
title: The 1D chart's session bounds follow the bars, not the calendar day — read from Yahoo's `tradingPeriods`, never `currentTradingPeriod` alone
status: Accepted
date: 2026-09-15
deciders: implementing agent (interactive session) + repo owner (bug report, live reproduction)
---

# 2026-09-15. Session bounds follow the bars, not the calendar day

## Context

The 1D chart's fixed visible range and its dotted pre/post-market bands are driven by
`MarketStatus.session_bounds` (`prices.market_status(with_session_bounds=True)`), which read the
pre/regular/post windows from the Yahoo chart payload's `meta.currentTradingPeriod`.

Owner report (2026-09-15): at 2pm SGT — 2am ET — the ORCL and RKLB 1D charts drew yesterday's
full 4am–8pm session but showed **no** pre/post dots. An earlier attempt (2026-09-09) had
removed a `state not in (PREPRE, POSTPOST)` gate in `market_status`, on the hypothesis that a
plain-`CLOSED` state was what blocked the bands. That fix was real but addressed the wrong
cause: the running server already had it, and the dots were still missing.

The actual mechanism, verified live against three venues past local midnight (Tokyo 7203.T at
~04:55 JST, Hong Kong 0700.HK, ASX BHP.AX — all `PREPRE`):

1. Yahoo rolls `currentTradingPeriod` to the **next, not-yet-started** session at **local
   midnight** — hours before that session opens. The code's standing premise (comment in
   `market_status`: "doesn't roll over to tomorrow's window until the exchange actually opens
   again", verified only at 10:55pm ET on the *same* calendar day) was empirically wrong past
   midnight.
2. The same payload's bars — and the 1D series the chart draws (`_yf_history(period="1d")`) —
   are still **yesterday's** session at that hour.
3. `market_status`'s "hasn't ended yet" guard (`regular.end > now` while closed → drop) then
   correctly refused to attach tomorrow's window, leaving no `session_bounds` at all → no fixed
   range → no bands. The guard was right; the input was wrong.

Yahoo's chart payload carries a second field, `meta.tradingPeriods`, on any **intraday**
interval request. It describes the trading periods **of the day(s) whose bars were returned**
— verified: for Tokyo/HK past midnight it said Sept 14 (matching the bars) while
`currentTradingPeriod` said Sept 15. `_fetch_chart` requested `interval=1d`, which omits it.

## Decision

We will derive `session_bounds` from the session the chart's own bars belong to, never from the
calendar-day window alone.

- `_fetch_chart` requests `interval=1h&range=1d&includePrePost=true` (was `interval=1d`). Only
  an intraday interval carries `meta.tradingPeriods`; the 1h payload is ~2 KB (vs ~1.5 KB), and
  `currentTradingPeriod` — what `_parse_chart_next_open` reads for the "reopens in …" chip —
  is present either way, so that path is untouched. Verified across asset kinds: equities,
  ETFs, indices, FX, futures, XETRA return `tradingPeriods`; a mutual fund (no intraday data)
  returns none and no error.
- `_parse_trading_periods` prefers `tradingPeriods` (most recent day; per segment first
  start → last end) and falls back to `currentTradingPeriod` only when `tradingPeriods` is
  absent or not the `{seg: [[…]]}` dict shape (an external-boundary check — a malformed shape
  must never 500 the asset-detail page).
- A zero-length window (`start == end` — what Tokyo, futures and FX report for pre/post) is
  dropped as "no such session", so the chart never receives a `pre`/`post` key to draw a band
  for. Previously such keys reached the client with zero width (visually nothing, but a
  fabricated `hasPre`/`hasPost`).
- The "hasn't ended yet" guard stays, as the safety net for the `currentTradingPeriod`
  fallback and any payload whose `tradingPeriods` somehow describe a future session.
- `_has_real_extended_bars` is unchanged: it counts real 1-minute bars inside the window from
  the same 1D series the chart draws, so with yesterday's bounds and yesterday's bars the
  "is this extended session real" verdict is judged on the right data.

## Consequences

### Positive

- Past local midnight (the 2am-ET / 2pm-SGT case), a closed symbol's 1D chart shows the dots
  for the session it is actually drawing. Live proof: 7203.T and 0700.HK now return Sept 14
  bounds at 04:55/03:55 local where before they returned none.
- The bounds and the bars are, by construction, the same session — the mismatch class that
  produced this bug and the earlier KSTL.DE/XETRA "window a full day ahead of the bars"
  regression (2026-09-01) can no longer arise from `currentTradingPeriod`'s rollover.
- Non-extended venues stop shipping degenerate `pre`/`post` keys.

### Negative

- Every `_fetch_chart` call (the reopens chip for every closed symbol in a batch, plus the
  asset-detail page) now pulls ~2 KB instead of ~1.5 KB. Same call count, same 20-minute
  cache; negligible.
- A mutual fund (no `tradingPeriods`) still goes through the `currentTradingPeriod` fallback
  and therefore still loses its bounds past local midnight. Funds have no intraday session to
  chart, so nothing visible is lost; noted for honesty.
- Yahoo's `tradingPeriods` shape is undocumented and could change; the parser degrades to the
  fallback rather than failing, which would silently reintroduce the midnight gap. The
  `test_fetch_chart_requests_an_intraday_interval` and
  `test_parse_trading_periods_prefers_the_datas_own_trading_periods_over_current` tests are
  the tripwires.

### Follow-ups

- The running :8080 instance (`make start`, no `--reload`) must be restarted to pick this up.
- `docs/qa/acceptance/intel.md` market-status paragraph updated in the same change.
- The 2026-09-09 "no live-state gate" change stays — it is correct and complementary (a
  weekend `CLOSED` still shows Friday's dots, via Friday's `tradingPeriods`) — but its
  original stated cause was wrong; the `market_status` comment now says so.

## Alternatives considered

- **Shift `currentTradingPeriod` back by one day when it describes a future session** —
  rejected: wrong on early-close days (the day before a holiday closes at 1pm; shifting a
  normal 4pm close back fabricates a session that didn't happen) and across weekends/holidays
  (which day is "one back" needs the exchange calendar we deliberately refuse to hardcode —
  owner ask 2026-08-31).
- **Derive bounds from the 1D series' first/last bar timestamps** — rejected: gives the pre
  start and post end but not the regular open/close boundaries the bands hinge on; a thin
  pre-market would also shrink the window to wherever the first print landed.
- **Keep `interval=1d` for `_fetch_chart` and add a second intraday fetch only on the
  session-bounds path** — rejected: two requests and two cache entries per symbol where one
  payload already carries both fields; the size difference is ~0.5 KB.
- **Widen `_yf_previous_session_bars`'s `session_date` into `market_status` to pick the day**
  — rejected: that fallback only fires for venues with zero 1D bars (XETRA pre-open), not the
  US case, and it would thread the history path into the market-status path (the ADR
  `2026-09-08-1d-chart-falls-back-to-the-previous-session` deferred exactly this coupling).
- **Relax the "hasn't ended yet" guard instead** — rejected: it was correct; attaching
  tomorrow's window to yesterday's bars is the KSTL.DE regression this repo already fixed once.

## Agent provenance

- **Decided by:** implementing agent, interactive session with the repo owner
- **Lane / branch:** `main` (direct, owner-driven bug fix)
- **Alternatives weighed:** shift `currentTradingPeriod` back a day · derive from bar
  timestamps · second intraday fetch · reuse `session_date` from the previous-session fallback
  · relax the future-window guard
- **Driving artifacts:** owner report "at 2pm SGT the ORCL/RKLB chart doesn't show the pre/post
  dots" with a reference screenshot of the wanted rendering; live probes of Yahoo's chart meta
  for RKLB, 7203.T, 0700.HK, BHP.AX, VTSAX, ^GSPC, EURUSD=X, SAP.DE, BRK-B, GC=F across
  `interval=1m/5m/1h/1d`; the superseded-in-spirit 2026-09-09 gate removal
  (`ec6b58a2a`) whose diagnosis this ADR corrects
- **Why this way (one line):** the bands must mark the session the chart is drawing, and the
  only Yahoo field that names that session is the one attached to the bars themselves.
