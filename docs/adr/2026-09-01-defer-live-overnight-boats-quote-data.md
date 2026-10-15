---
number: N/A
title: Defer live overnight (BOATS) quote data — document the research, don't build yet
status: Accepted
date: 2026-09-01
deciders: orchestrating agent (Claude) + repo owner
---

# Defer live overnight (BOATS) quote data

## Context

While fixing the asset-detail page's after-hours display (`backend/services/prices.py`'s
`_extended_hours_from_quote` and `market_status(with_session_bounds=True)` — both fixed the same
day this ADR was written, to widen PRE/POST handling to PREPRE/POSTPOST and keep `session_bounds`
attached after close), the owner asked whether we can also show the genuinely LIVE-updating
"Overnight" price Yahoo Finance's own website displays late in the evening — distinct from the
frozen `postMarketPrice` we already show, which freezes at the standard 8pm ET post-market close
and never updates again.

This app's entire price-fetching surface (`_yf_batch_quote`, `_fetch_chart`, everything in
`backend/services/prices.py`) is request/response polling today. The question was whether the
overnight/BOATS (Blue Ocean ATS) data Yahoo now shows for major US names is reachable the same
way, or requires something architecturally different.

## Decision

We will **not** build genuine live overnight/BOATS quote data now. We document the research
below so a future session can pick this up without redoing the investigation.

## Findings (all independently verified live, not just researched)

- Neither the v7 quote endpoint (`_yf_batch_quote`) nor the v8 chart endpoint (`_fetch_chart`) —
  the only two Yahoo endpoints this codebase currently calls — expose overnight data in any
  field. Confirmed by direct inspection of both raw responses for MSFT during a live overnight
  window, and re-confirmed *after* watching a real overnight trade happen via the websocket
  below: `postMarketPrice`/`postMarketTime` in the v7 quote stayed frozen at the 8pm print the
  whole time.
- Two community attempts to get this data via `prepost=True` historical-bar filtering —
  [yfinance PR #2640](https://github.com/ranaroussi/yfinance/pull/2640) (closed, not merged; the
  maintainer called it a one-line pandas filter with no real value) and its downstream copy in
  [carnat/yahoo-finance-mcp PR #27](https://github.com/carnat/yahoo-finance-mcp/pull/27) (merged)
  — both only work for 24/7 assets like crypto. Their own docs/tests admit real equities return
  `None`, because Yahoo's chart/bar data has no bars at all between the 8pm close and the next
  day's pre-market.
- The real data does exist, but only via Yahoo's public real-time WebSocket:
  `wss://streamer.finance.yahoo.com/?version=2`, JSON-wrapped base64-encoded protobuf
  (`PricingData`, schema already vendored in the installed yfinance 1.7.0 as
  `yfinance/pricing_pb2.py`), already wrapped by `yfinance.WebSocket`/`AsyncWebSocket`
  (`yfinance/live.py`). Same public-undocumented-endpoint posture as the REST calls this codebase
  already makes — not scraping.
- Verified live, directly, twice (once by a research subagent, once by the orchestrating agent
  connecting itself): subscribing to MSFT during a deep-overnight window returns real, distinct
  ticks (e.g. price 506.75, later 506.88, later back to 506.75) carrying a `market_hours: 4`
  field — inferred, not documented anywhere (not in yfinance's own comments, README, or issues),
  to be the overnight-session marker. A simultaneously-subscribed BTC-USD (always trading) showed
  `market_hours: 1` ticking continuously and far more frequently — ~8 messages in the same 20s
  window MSFT sent exactly 1 — consistent with genuinely thin but real overnight equity volume,
  not a stuck or fake feed.
- The open yfinance feature request for this is
  [issue #2630](https://github.com/ranaroussi/yfinance/issues/2630) — still open, unresolved, no
  maintainer solution, nobody there has connected it to the websocket.

## Consequences

### Positive
- No new architecture (persistent connections, reconnect/backoff, a live-push cache) added to
  `prices.py` before it's actually decided to be worth the cost.
- The research is captured once, durably, instead of living only in a chat transcript that will
  be gone once this session ends.

### Negative
- The asset-detail page's after-hours figure stays frozen at the 8pm close through the rest of
  the evening/overnight, even though Yahoo's own site shows a fresher (if thin-volume) number for
  major US names during that window. This is a real, known gap, not an oversight.

### Follow-ups
- If/when this is prioritized: build a persistent background WebSocket connection manager
  (subscribe list, reconnect/backoff, the library's own 15s heartbeat resubscribe), decide where
  the pushed value gets cached/served (the existing market-status cache vs. a new field), then a
  thin read-path change on top. Rough estimate: 1-2 days for a solid implementation with
  reconnect handling and tests.
- Before relying on `market_hours == 4` for real logic: confirm it's stable by watching it change
  to a different value once the next pre-market/regular session starts (it's inferred from one
  evening's observation, not a documented contract, and could shift without notice).

## Alternatives considered

- **`prepost=True` historical-bar filtering** — rejected. Already proven not to work for real
  equities by two independent community attempts (see Findings above); only works for 24/7 assets.
- **Scrape the rendered `finance.yahoo.com` page** — rejected. Out of keeping with this
  codebase's existing "undocumented-but-public JSON endpoint" posture (v7/v8, and now the
  websocket); scraping rendered HTML is a different and worse posture to take on.
- **Build the WebSocket integration now** — rejected for this pass. Real, working, and scoped
  (1-2 days), but a genuine architectural addition (this codebase's price-fetching is 100%
  request/response today) that deserves its own deliberate build, not something to bolt on while
  mid-fix on the unrelated after-hours-display bug that prompted this investigation.
- **Do nothing / leave the gap silently** — rejected in favor of this ADR: leaving it undocumented
  would mean the next person to ask "can we get overnight data" redoes this entire investigation
  from scratch.

## Agent provenance

- **Decided by:** orchestrating Claude agent (interactive session), confirmed by repo owner
- **Lane / branch:** `main` (interactive session, not a lane/worktree build)
- **Alternatives weighed:** `prepost=True` bar filtering vs. HTML scraping vs. building the
  WebSocket now vs. documenting and deferring (this ADR)
- **Driving artifacts:**
  [yfinance#2630](https://github.com/ranaroussi/yfinance/issues/2630),
  [yfinance#2640](https://github.com/ranaroussi/yfinance/pull/2640),
  [carnat/yahoo-finance-mcp#27](https://github.com/carnat/yahoo-finance-mcp/pull/27), a research
  subagent's report, and the orchestrating agent's own live WebSocket connection/verification
- **Why this way (one line):** the data is real and reachable, but only via a persistent push
  connection this codebase's price layer doesn't have yet — that's a deliberate build, not a
  side-quest fix, so the research gets captured now and the build gets scoped later.
