---
title: The 1D chart falls back to the previous COMPLETE session, distinctly flagged, when a non-extended-hours venue has zero bars before its regular open
status: Accepted
date: 2026-09-08
deciders: implementing agent (fix/1d-price-history-previous-session-fallback lane) + repo owner (bug report, root cause pre-supplied)
---

# 2026-09-08. The 1D chart falls back to the previous session, distinctly flagged, instead of a hard error

## Context

KSTL.DE (XETRA) viewed before the regular session opens (or shortly after it closes) showed the
hard "Price history unavailable" empty state, even though Yahoo's own website drew a chart for the
same symbol at the same moment. Root cause chain, already established before this change:

1. `_yf_history()` requests `period="1d"` for the 1D timeframe (`interval="1m"`, falling back to
   `"5m"` — `_YF_OHLCV_INTERVAL["1D"]`). XETRA has `hasPrePostMarketData: false`, so before the
   regular session opens there is no pre-market print either — Yahoo's chart API returns ZERO bars
   for BOTH interval probes, unlike a US ticker which at least has a pre-market tick to show.
2. `fetch_history_result()` turns that empty result into `state: "unavailable"`,
   `degraded_reason: "no_data"`/`"upstream_unavailable"`.
3. `asset_detail.price_series()` deliberately skips the locally-saved daily `PriceHistoryStore`
   fallback for the 1D timeframe (`if not points and timeframe != "1D" ...`) — correctly, since
   that store holds daily closes with no intraday granularity to offer a 1D chart.
4. The client-side seeded prev-close → live line (ADR
   `2026-09-03-seeded-open-line-beats-unavailable`) only fires when `marketOpen === true`. Here the
   anti-phantom-print freshness gate (`_quote_to_market`, `prices.py`) correctly reports the market
   as CLOSED, so that seed never fires either — it solves a different problem (a delayed feed
   during an open session), not this one (no session running at all).
5. With no drawable points and `state: "unavailable"`, `AssetPriceChart` renders the hard error.

Both existing mitigations legitimately don't apply: the daily-close fallback lacks intraday
resolution, and the seeded line requires the market to be open. Yahoo's own web frontend evidently
draws through this exact window by quietly falling back to the prior session — so the fix is to do
the same, honestly labeled rather than presented as live.

## Decision

We will widen the 1D fetch and serve the most recent COMPLETE trading session's real bars,
flagged distinctly so the client never presents it as live data.

- `_yf_previous_session_bars(symbol)` (`backend/services/prices.py`) fires only after `_yf_history`
  comes back empty for `timeframe == "1D"` on a non-crypto instrument. It requests `period="5d"`
  with the SAME `interval` fallback chain (`1m` → `5m`, still `prepost=True`), groups the returned
  bars by calendar date in the exchange's own tz (`h.index.date`, `h.index.tz`), and returns only
  the bars for the single most recent date strictly before today — **never today's own bucket**,
  even a stray one-off after-hours print that happens to land there. `([], None)` when even the
  widened window has no session older than today (a genuinely dead/delisted symbol).
- `fetch_history_result` wires this in as a fallback of last resort: it only runs when the live 1D
  fetch produced nothing AND there's no better option already available (a fresher same-day cache
  entry — real or stale — always wins; see "Alternatives" for why). The result carries
  `state: "stale"` (reusing the existing 3-value enum rather than widening it for one reason code)
  plus a NEW `degraded_reason: "previous_session"` and a new `session_date` field (the exchange's
  own ISO calendar date) — the two together are what let the client tell this apart from every
  other stale case. Memoized per symbol for `_HISTORY_TTL` (30 min) in a cache dict SEPARATE from
  the live-fetch cache, so a still-closed venue's 60s poll doesn't re-issue the 5-day widened fetch
  every time, and a later transient failure of the (unrelated) live fetch never gets mislabeled
  with this reason by reusing a shared slot.
- The frontend (`AssetPriceChart.tsx`) treats `degraded_reason === 'previous_session'` as its own
  branch, checked before the generic stale-series banner: it draws the chart (never the hard
  "Price history unavailable" empty state) with a small `Previous session` badge — new CSS class
  `.asd-market-prevsession`, `--warn`-tinted the SAME as the generic stale banner right above it in
  the same file (design review 2026-09-08 amended this from an initial calm `--accent` tint: this
  codebase's own convention — `FreshnessChip.tsx`, and the sibling `MarketSeriesStatus`/
  `.asd-market-stale` banner in this exact file — treats "not today's data" as warn-tone, never
  calm; a calm gray badge risked reading as current). The copy stays deliberately CAUSE-AGNOSTIC —
  "Showing Fri's session — today's session has no data yet." rather than asserting "hasn't
  started" — because `fetch_history_result` cannot itself distinguish "the session genuinely
  hasn't started" from "the live fetch failed while the market was actually open" (see
  Consequences: Negative). The `aria-live` announcement gets its own sentence for the same reason.
  The true "nothing anywhere, not even a prior session" case (`state: "unavailable"`) is completely
  unchanged and still shows the existing hard error.

## Consequences

### Positive

- A user opening a foreign-exchange holding before its local open sees a real, recent chart
  instead of an error — matching what Yahoo's own site already shows for the same symbol.
- The distinction is structural, not cosmetic: a caller inspecting `degraded_reason` (this repo's
  existing vocabulary: `no_data` / `upstream_unavailable` / `local_history`, now plus
  `previous_session`) can always tell real-but-not-today apart from genuinely live.
- No new state added to `MarketDataState` — the 3-value enum (`live`/`stale`/`unavailable`) stays
  as-is; `degraded_reason` already existed as the finer-grained signal and absorbs the new case
  without a breaking type change for any other consumer of that union.

### Negative

- A closed non-extended-hours venue now costs one extra (bounded, memoized) 5-day widened Yahoo
  fetch per symbol per 30 minutes, instead of failing fast to "unavailable". Acceptable: it only
  fires when the cheap 1D fetch already came back empty, and `_prev_session_cache` memoizes BOTH
  outcomes — a positive `(points, session_date)` for `_HISTORY_TTL` (30 min) and a negative
  `([], None)` for the shorter `_HISTORY_FAIL_TTL` — so the cost is bounded regardless of poll
  frequency. Security review (2026-09-08) caught a real gap in the first cut here: the negative
  outcome went uncached, and the live-fetch back-off branch that exists specifically to bound
  retries during a rate-limited window was ALSO issuing a fresh widened fetch through it — meaning
  a Yahoo 429 would make this fallback triple the outbound request rate instead of backing off.
  Fixed by caching negatives too and giving `_previous_session_response` an `allow_fetch=False`
  mode the back-off branch uses exclusively (only the post-live-fetch path may trigger the
  widened fetch). `_prev_session_cache` is also capped (`_PREV_SESSION_CACHE_MAX`, pruning expired
  rows first) since it's keyed by a symbol reachable from an LLM tool with no cap of its own.
- `state: "stale"` is now overloaded with two meanings the client must further discriminate via
  `degraded_reason` (a failed live refresh serving old cached bars, vs. a clean previous-session
  fallback) — a `MarketDataState` widened to 4 values would have made this compile-time explicit,
  at the cost of touching every switch over that union. Rejected for now (see Alternatives); worth
  revisiting if a third `degraded_reason` needs the same treatment.
- The "which session is this?" disclosure depends on the exchange's own calendar date arriving
  intact from `h.index.date` — a yfinance/pandas index that came back tz-naive would misattribute
  "today" using UTC instead of the exchange's local day. Not observed in practice (yfinance
  localizes intraday bars to the exchange tz), left unguarded rather than adding defensive code for
  a case that hasn't occurred.
- `fetch_history_result` cannot tell "the session genuinely hasn't started/has no data yet" apart
  from "the live fetch failed while the market was actually open" — it has no access to
  market-open/session-bounds state (that lives in the separate quote/market-status path). A cold
  cache right after a restart could in principle serve yesterday's session while today's data
  exists but the live fetch merely failed. Mitigated, not solved: the copy is written
  CAUSE-AGNOSTIC ("has no data yet", not "hasn't started") so it never asserts a reason it can't
  verify. Threading market-status into this path to gate the fallback on a KNOWN-closed venue was
  considered and deferred (see Alternatives) — the softened copy was judged sufficient given the
  actual window this hits (this fallback only fires when Yahoo's OWN chart API already returned
  zero bars for the live probe, which is a strong independent signal in its own right).

### Follow-ups

- None required to ship. If a future venue's `hasPrePostMarketData` handling changes the shape of
  the "closed with zero bars" window, `_yf_previous_session_bars`'s "most recent date before today"
  rule should still hold — worth re-checking against that venue specifically rather than assumed.

## Alternatives considered

- **Widen `MarketDataState` to a 4th value (e.g. `"previous_session"`)** — rejected: touches every
  switch over the existing 3-value union (`seriesState`, `shouldCacheSeries`, `cacheSeries`,
  `chartEmptyReason`) for a distinction `degraded_reason` (already a free-text field, already
  carrying `"local_history"` for an analogous "real-but-not-live" case) can express with zero type
  changes.
- **Extend the local `PriceHistoryStore` daily-close fallback to serve 1D too** — rejected: that
  store holds ONE close per day, no intraday bars — it cannot produce a session's shape, only a
  single point. `asset_detail.py`'s existing 1D exclusion for that fallback is correct and stays.
  Its own reasoning is documented at that call site.
- **Relax the seeded prev-close → live line (ADR 2026-09-03) to fire when the market is closed
  too** — rejected: that seed is a 2-point synthetic line anchored on the session-open grid slot;
  drawing it while genuinely closed would fabricate a flat line with no real intraday shape, when
  a real previous session's actual bars are fetchable instead. Reusing real data beats inventing a
  synthetic substitute wherever real data is available.
- **Serve the widened fetch unconditionally on every 1D request, not just as an empty-result
  fallback** — rejected: doubles the request cost (the initial narrow `period="1d"` probe stays
  strictly cheaper and succeeds the overwhelming majority of the time — this only fires in the
  specific pre-open/post-close window for a non-extended-hours venue).
- **Cache the previous-session result in the same `_history_cache` dict as the live fetch** —
  rejected: a later transient failure of the (unrelated) live fetch would read that cache entry
  back through the existing "stale-but-real" path and mislabel it `degraded_reason:
  "upstream_unavailable"`, losing the "this is yesterday's session" disclosure. Given a separate
  dict instead.
- **Thread market-open/session-bounds state into `fetch_history_result` to gate the fallback on a
  KNOWN-closed venue** (security review 2026-09-08, to fix the "can't tell closed from failed"
  ambiguity above at the root) — deferred, not rejected outright: that state lives in the separate
  quote/market-status path (`_quote_to_market`), not this history-fetch path, and every caller of
  `fetch_history_result` (the REST series endpoint, `yahoo_get_price_history`,
  `coingecko_get_price_history` indirectly) would need to thread it through. Softening the copy to
  not assert a cause closes the actual harm (a misleading claim) at far lower risk than reshaping
  this function's signature; worth revisiting if the ambiguity itself (not just the wording) turns
  out to matter in practice.
- **Bare `dict`/`Optional` sentinel instead of a `session_date is None` convention for a negative
  `_prev_session_cache` entry** — rejected: `_yf_previous_session_bars` already guarantees
  `session_date` is set if and only if `points` is non-empty (never both/neither independently),
  so reusing that existing invariant as the cache's own positive/negative marker needs no new
  sentinel type or extra tuple field.

## Agent provenance

- **Decided by:** implementing agent, `fix/1d-price-history-previous-session-fallback` lane
- **Lane / branch:** `fix/1d-price-history-previous-session-fallback`
  (worktree `.claude/worktrees/agent-a06a960b86301515e`)
- **Alternatives weighed:** widen `MarketDataState` to 4 values · extend the daily `PriceHistoryStore`
  fallback to 1D · relax the seeded-open-line ADR to fire while closed · always widen the 1D fetch
  · share the live-fetch cache · thread market-status into the fallback to gate on known-closed
- **Driving artifacts:** owner bug report (KSTL.DE/XETRA pre-open showing the hard error while
  Yahoo's own site draws a chart), pre-supplied root-cause chain through `prices.py`/
  `asset_detail.py`/`priceTail.ts`/`AssetPriceChart.tsx`; ADR
  `2026-09-03-seeded-open-line-beats-unavailable` (the related, narrower "market open, feed
  delayed" case this decision is deliberately NOT extending); a same-branch multi-agent
  verification pass (code-reviewer, qa-engineer, ui-designer, security-reviewer) that caught the
  uncached-negative rate-limit gap, the calm-badge misread risk, and the cause-asserting copy —
  all folded into this same ADR rather than filed as separate follow-ups, since none had shipped
  yet
- **Why this way (one line):** real data from the last complete session beats both an error and a
  fabricated seed line — as long as it is never allowed to look like today's live tape, is never
  mistaken for a reason to hammer a rate-limited upstream harder, and is never colored like it's
  current when it isn't.
