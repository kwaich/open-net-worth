# CMD-01 — Resilient crypto market-data failover

`CMD-01` is the blocking production-build acceptance proof for the Bitcoin
asset-detail market-data path. It boots a disposable QA backend with
`ONW_QA_MODE=1`; only the upstream provider boundary is deterministic. The
browser still uses the real resolve route, real asset route, real persistent
series cache, real Settings API, and built frontend.

## Acceptance criteria

### CMD-01-YAHOO-LIVE

- GIVEN canonical Bitcoin is resolved through `POST /api/intel/assets/resolve`
- WHEN the QA scenario is `yahoo_live`
- THEN `GET /api/intel/assets/{id}/series?timeframe=1D` reports
  `state=live`, `provider=yahoo`, and at least three provider observations;
  the rendered asset screen shows `Live`, `Yahoo Finance`, a freshness label,
  and a chart container.

### CMD-01-CG-FALLBACK

- GIVEN the scenario is `yahoo_fail_cg_live`
- WHEN Bitcoin's selected series is fetched
- THEN the API reports `state=live`, `provider=coingecko`, the rendered screen
  shows `Live fallback` and `CoinGecko`, and QA call counts show one Yahoo and
  one CoinGecko history attempt.

### CMD-01-STALE-RELOAD

- GIVEN a successful CoinGecko fallback has persisted a complete valid series
- WHEN Yahoo fails and CoinGecko returns HTTP 429
- THEN the response is `state=stale` with the complete saved point shape and
  the screen says `Saved data` / `Live providers are temporarily unavailable.`;
  QA evidence records one Yahoo attempt followed by one CoinGecko attempt, and
  `telemetry_24h` increments the fallback, HTTP-429, and stale-serve counters.
- WHEN the actual browser hard-reloads
- THEN the same state and complete saved shape render from persistent cache.

### CMD-01-UNAVAILABLE-NO-SYNTHETIC

- GIVEN the persistent Bitcoin cache is cleared and both providers fail
- WHEN the asset screen is opened
- THEN the API reports `state=unavailable`, `provider=null`, and `points=[]`;
  the toolbar remains visible with `Price history unavailable` and `Try again`,
  while no chart container, chart canvas/path, tooltip, or synthetic point
  exists. (The unavailable-state icon itself is not chart data.)

### CMD-01-RECOVERY-AND-METADATA-ISOLATION

- GIVEN an unavailable attempt has occurred
- WHEN Yahoo recovers
- THEN Bitcoin returns to `live` / `yahoo` with real provider points.
- GIVEN the scenario is `metadata_fail`
- WHEN the real asset detail is fetched and its actual `POST .../refresh?force=true`
  path is invoked
- THEN neither endpoint returns a 5xx, QA records at least one metadata-provider
  call, and a healthy Yahoo price series remains `live` / `yahoo`; a metadata
  failure must not poison tape state. (A no-model QA refresh may honestly return
  `refreshed=false`; that is distinct from an HTTP failure.)

### CMD-01-IDENTITY-AND-PROVIDER-COUNTS

- THEN canonical Bitcoin remains `price_source_id=bitcoin` while Yahoo is the
  provider retrieval alias, not the instrument identity.
- AND the explicit PEPE flow resolves canonical `pepe`; its configured Yahoo
  pair is `PEPE24478-USD`, never inferred `PEPE-USD`, without changing that
  canonical identity. CMD-01 exercises the public Yahoo series result while
  `tests/test_crypto_market_data.py::test_pepe_usd_history_prefers_yahoo_alias_and_caches_canonical_id`
  pins the exact non-derivable provider pair and canonical cache key.
- AND each deterministic provider scenario records the expected provider call
  counts via `GET /api/qa/market-data/status`. Its QA-only, secret-free request
  evidence records canonical id and provider retrieval id, so CMD-01 can prove
  `pepe` requested `PEPE24478-USD` without making that alias user identity.

### CMD-01-SETTINGS-SECRET-AND-ROLLBACK

- WHEN a Demo API key is saved through the actual Settings panel
- THEN the API only returns `has_coingecko_demo_key=true`; the secret is absent
  from API response bodies and rendered DOM, the password control is blank or
  masked, and blank save is disabled.
- WHEN Yahoo-first is disabled, the Settings banner says `Rollback active`;
  after reload it remains disabled. In the deterministic live fallback, only
  CoinGecko is called because it succeeds; in deterministic `both_fail`, the
  attempted rollback order is CoinGecko then Yahoo before stale data is served.
  Re-enabling persists Yahoo-first and the `yahoo_fail_cg_live` sequence proves
  Yahoo then CoinGecko order.

### CMD-01-RESPONSIVE-EVIDENCE

- At 390px, the stale and unavailable asset states have no horizontal overflow,
  and the Settings provider rows/key controls stack as one column without
  overflow.
- At 640px, 1024px, and 1440px, both the asset chart and Market Data Settings
  remain usable with Lucius closed and open; the runner records every state in
  the responsive evidence matrix.

### CMD-01-MALFORMED-AND-CONCURRENT

- GIVEN both upstream payloads are malformed, the public series route returns
  honest `unavailable` with no synthetic chart points or server error.
- GIVEN two identical delayed history requests overlap, both responses are
  identical and QA provider counts prove a single Yahoo attempt and a single
  CoinGecko fallback attempt; `telemetry_24h.coalesced` increments.
- The final public status response exposes secret-free bounded 24-hour counters
  showing at least one Yahoo call, CoinGecko call, fallback, 429, stale serve,
  unavailable result, and coalesced request from this run.

## Required evidence

The deterministic runner writes ignored evidence under
`qa/artifacts/crypto-market-data-failover/`:

- `CMD-01-yahoo-live.png`
- `CMD-01-hard-reload-stale.png`
- `CMD-01-unavailable.png`
- `CMD-01-settings-demo-key.png`
- `CMD-01-mobile-stale.png`
- `CMD-01-mobile-unavailable.png`
- `CMD-01-mobile-settings-demo-key.png`
- `CMD-01-chart-chat-closed-640.png`
- `CMD-01-chart-chat-closed-1024.png`
- `CMD-01-chart-chat-closed-1440.png`
- `CMD-01-chart-chat-open-640.png`
- `CMD-01-chart-chat-open-1024.png`
- `CMD-01-chart-chat-open-1440.png`
- `CMD-01-settings-chat-closed-640.png`
- `CMD-01-settings-chat-closed-1024.png`
- `CMD-01-settings-chat-closed-1440.png`
- `CMD-01-settings-chat-open-640.png`
- `CMD-01-settings-chat-open-1024.png`
- `CMD-01-settings-chat-open-1440.png`

Run with `cd frontend && npm run e2e:crypto-market-data`. It prints
`crypto-market-data-e2e: PASS` only when every API and DOM assertion passes.
