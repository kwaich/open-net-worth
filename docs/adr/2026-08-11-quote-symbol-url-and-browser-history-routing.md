# `/quote/$symbol` replaces the hash URL for ticker-backed asset detail

- **Date**: 2026-08-11
- **Status**: Accepted
- **Area**: Frontend routing (`frontend/src/router.tsx`, `RouterShell.tsx`,
  `state/assetNav.tsx`), asset-detail data flow (`hooks/useResolveTicker.ts`,
  `components/intel/QuoteDetailView.tsx`), backend (`api/intel_assets.py`,
  `services/instruments.py`, `db/instrument_store.py`), SPA serving (`backend/main.py`)

## Decision

Two changes, shipped together because the second only works once the first is in place:

1. **Hash routing is retired.** `router.tsx` switches TanStack Router from
   `createHashHistory()` to `createBrowserHistory()` — URLs read
   `/quote/ONWAI.DE` instead of `/#/asset/e40e64f8-...?ticker=ONWAI.DE`. A one-time client-side
   migration rewrites an old `#/...` bookmark to the equivalent path on load. Since hash
   fragments never reach the server, nothing server-side needed to know about client routes
   before; browser-history routing means a refresh or deep link on `/quote/ONWAI.DE` is now a
   real request the server must answer. `backend/main.py` gains `_SPAStaticFiles`, a
   `StaticFiles` subclass that falls back to `index.html` for any unmatched GET path except
   `api/*` (which stays a real 404 — a `Mount` bypasses the app-wide auth dependency entirely,
   so swallowing a genuine API 404 into the HTML shell would be actively misleading, not just
   imprecise).

2. **A new route, `/quote/$symbol`, is the canonical URL for any ticker-backed asset** —
   mirroring Yahoo Finance's `/quote/AAPL` (owner's explicit naming choice; "ticker" was the
   working name during design). `/asset/$instrumentId` is kept, unchanged, as the fallback for
   instruments with no ticker at all (`price_source="manual"`, which stores
   `price_source_id=""` — a real code path, not hypothetical: `services/instruments.py:357-376`
   and `services/asset_detail.py`'s `has_ticker` branch both exist to support it, even though
   this owner's portfolio holds none today). `$symbol` is the source-qualified id already
   carried as the old route's `ticker` search param (e.g. `ONWAI.DE`, not bare `ONWAI`) — see
   "Why price_source_id, not the bare symbol" below.

   Navigating to `/quote/$symbol` **never waits on a network call first.** The two central
   navigate helpers (`RouterShell.tsx`'s `openAssetDetail`, `state/assetNav.tsx`'s `openAsset`)
   call `navigate()` synchronously the instant a ticker is known; the destination route resolves
   itself via one new endpoint, `GET /api/intel/assets/by-ticker/{ticker}`
   (`services.instruments.resolve_by_ticker` + `services.asset_detail.read_asset_detail` in one
   round trip), rendering its own skeleton/error state exactly as `AssetDetailView` already does
   for its own fetch (`hooks/useResolveTicker.ts`, `components/intel/QuoteDetailView.tsx`). This
   deleted the old resolve-then-navigate flow (a `POST /api/intel/assets/resolve` round trip
   blocking navigation, duplicated across `RouterShell.tsx` and `assetNav.tsx`, plus a
   resolving/busy affordance in `AssetLink.tsx` that existed purely to make that wait not look
   like a dead tap) — owner requirement: "not that user has to wait for resolving in the current
   tab before going to the new one."

## Why

**The `#` and the opaque id were both explicitly unwanted.** The owner's own words: removing the
hash was step one ("i don't want to have it in the url, it's weird"); the id-based URL was step
two, and the reasoning given was that `/asset/<uuid>` or `/asset/ins_...` "will take time to
resolve" — meaning a human (or an AI) reading the URL cannot tell what it points to without a
lookup, unlike `/quote/ONWAI.DE`, which is self-describing and shareable.

**Why price_source_id, not the bare `symbol`.** The obvious simpler design — use the plain ticker
symbol in the URL — was rejected on evidence, not suspicion. This owner's own instrument store
(61 instruments, checked before writing any code) has a pre-existing collision:
two distinct instruments both carry `symbol="oklo"` case-insensitively — the real NYSE stock
(`price_source="yahoo"`, `price_source_id="OKLO"`) and what looks like a Robinhood tokenized-stock
proxy (`price_source="coingecko"`, `price_source_id="oklo-inc-robinhood-token"`). Neither is
currently held, so it would have been invisible without checking. The CommandPalette's Markets
search already independently discovered and coded around the same class of bug (its result-id
comment: "the backend can legitimately return several DISTINCT results sharing one ticker...
Backend dedup already keys on (price_source, price_source_id)"). `price_source_id` had **zero**
collisions across all 61 instruments (case-insensitive), matching `instrument_store.py`'s own
module docstring: it "never collides the way a human `symbol` can across exchanges." The owner
also confirmed they will only ever reference real, exchange-qualified market tickers
(`ONWAI.DE`-style) — never a shorthand — which is exactly the shape `price_source_id` already is.

**Why a new store/service method instead of routing through the existing `resolve_instrument`
ladder.** `resolve_instrument(query)` with no source hint is a fuzzy, provider-search-capable
ladder (Yahoo/CoinGecko lookups, symbol-based dedupe) — correct for a genuinely new ticker, but
needless (and, given the collision above, unsafe) for the common case: an instrument that
already exists. `InstrumentStore.find_by_price_source_id` (new) does a direct, vendor-agnostic
exact match; `services.instruments.resolve_by_ticker` (new) tries that first and only falls
through to `resolve_instrument`'s find-or-create ladder when nothing matches.

**A second gap found post-implementation, twice: several UI components pass a bare display
symbol, not the qualified id, as `ticker`.** First found for crypto —
`services.positions._row_ticker` deliberately substitutes the display symbol ("BTC") for the
CoinGecko id ("bitcoin") on every held-crypto row, so it *reads* right elsewhere in the app.
Then an independent `/code-review` pass found the same shape again in an equity context:
`commitments/fundingValue.ts` builds its `PositionMeta.symbol` as `r.symbol || r.ticker`
(bare-symbol-first), so the Commitments funding-source link passes `"ONWAI"`, not `"ONWAI.DE"`.
Either way, the bare form misses `find_by_price_source_id`'s exact match and would otherwise
fall all the way through to the network-hitting ladder on *every single open* of an
already-known holding — exactly the cost this ADR's fast path exists to avoid, and a
reliability regression versus the old flow (that Yahoo/CoinGecko search call could fail or
rate-limit an open that used to be instant and local).

Rather than chase every such call site, `resolve_by_ticker` closes this generally: a new store
method, `InstrumentStore.find_all_by_symbol(symbol)`, returns *every* instrument (any asset
class) sharing a bare symbol case-insensitively. When exactly one comes back, it's unambiguous
and safe to use directly — no network call, no guess. When it's shared (the real "oklo" stock
vs. its tokenized-wrapper crypto twin — two distinct instruments, same bare symbol) or unknown,
this falls through to the full ladder rather than picking one blindly. (The "oklo" pair itself
never actually reaches this fallback: the stock's own `price_source_id` IS `"OKLO"`, so the
exact-match step above resolves it correctly first — the fallback only matters when *neither*
colliding instrument's `price_source_id` equals the bare symbol being queried.) This is
deliberately more general than an earlier, crypto-specific version of this fix (gated on
`price_source == "coingecko"`, mirroring `_row_ticker`'s own gate) — that version fixed the
first-found gap but not the second, since `fundingValue.ts`'s equity case isn't crypto at all.
Verified against a cloned, secret-stripped copy of the owner's portfolio: all six held coins (BTC,
DOGE, ETH, PEPE, SHIB, TLM) resolve to their correct, distinct instrument ids in ~50ms, no
network round trip.

## Alternatives considered

- **Ticker + explicit asset-class/exchange qualifier in the URL** (`/quote/ONWAI.DE?type=stock` or
  similar), to pre-empt any future collision. Rejected as unwarranted complexity: the identity
  key already used everywhere else in this codebase (`price_source_id`) already carries enough
  qualification in practice (proven empirically above), and the owner's own usage pattern
  (real, exchange-suffixed tickers only) doesn't need it. Revisit if a genuine `price_source_id`
  collision is ever observed.
- **Redirect `/quote/$symbol` to `/asset/$instrumentId` once resolved.** Rejected — it defeats
  the entire point (a shareable, bookmarkable, human-legible URL) if the address bar rewrites
  itself to the opaque id the moment the page loads.
- **Keep the POST /resolve round trip before navigating, just retarget it at the new route.**
  Rejected per the owner's explicit mid-task correction: navigation must be instant; resolution
  belongs on the destination screen, which already has first-class loading-state UI for exactly
  this (`AssetDetailSkeleton`).

## Consequences

- `AssetDetail` (`types/assetDetail.ts`, and the shared backend response builder in
  `services/asset_detail.py`) now always carries `instrument_id` — additive, harmless for the
  existing `/asset/{id}` callers, and required so a page that only knows a ticker can key its
  subsequent series/stats/refresh/watchlist calls (all `instrumentId`-shaped) off the resolved
  value.
- `useAssetDetail.ts` exports `seedDetailCache` so `useResolveTicker`'s one-hop response can
  pre-warm the id-keyed cache — the subsequent `AssetDetailView` mount paints instantly instead
  of a second, redundant GET-by-id before first paint (it still quietly revalidates in the
  background, the same stale-while-revalidate path a reopen already uses).
- Any caller that supplies both a ticker and an instrument_id (common — most held-position rows
  know both) now lands on `/quote/$symbol`, not `/asset/$instrumentId` — ticker wins whenever
  present. Only a genuinely tickerless instrument falls back to the id route.
- `AssetLink`'s resolving/busy affordance (opacity dim + `aria-busy` + a dedup-in-flight ref) is
  gone along with the `.asset-link--resolving` CSS rule — there is no longer an async wait for it
  to cover.
- Crypto's price chart no longer draws a labeled "Prev close" dashed reference line (owner call:
  a 24/7 asset has no closing bell, so the label is misleading) — `AssetPriceChart` gained a
  `showPrevCloseLine` prop, false for `asset_class === 'crypto'`. The underlying `prevClose`
  *value* is still passed through unconditionally and still drives the 1D line's up/down color
  (`seriesBaseline`) so it keeps agreeing with the header's % change — only the visible
  annotation is asset-type-specific, not that internal comparator (a reported bug, 2026-07-17,
  is exactly what happens when they disagree: header red, chart line green). The two concerns
  are split into separate pure helpers in `assetPriceChartLwc.ts` — `seriesBaseline` (unchanged)
  and `shouldShowPrevCloseLine` (new) — each independently unit-tested.
- Separately (not this route's URL scheme, but found and fixed while verifying it against real
  crypto price data): `services.asset_detail._price_block` was converting a per-unit crypto
  price through `core.fx.to_base`, which rounds to 2dp — correct for a monetary amount, wrong
  for a price. `to_base(2.87e-06, ...)` (a real PEPE-scale price) rounded straight to `0.0`,
  which then read as "no price" on the frontend. Fixed by doing the same pivot conversion
  inline with `_round_price` (significant-figure rounding) instead of calling `to_base` for a
  price — `to_base` itself is untouched, since it's correct everywhere else it's used (position
  values, cost basis) and touching a money-critical shared utility for one mismatched call site
  would have been the wrong trade.

## Agent provenance

Decided by a Claude agent in conversation with the owner: the owner set the two headline
decisions directly (drop the `#`; a new non-`/asset/` route; name it `/quote/$symbol` after
Yahoo Finance's convention; navigation must never block on a resolve call). The agent's own
contribution was the identity-key choice (`price_source_id` over bare `symbol`), grounded by
querying the owner's own DB rather than assuming — which surfaced the `oklo`/`OKLO`
collision — and the resulting `resolve_by_ticker` fast-path design, later generalized (bare
symbol -> id, any asset class, not just crypto) in response to an independent finding from an
automated `/code-review` pass over the branch. The zero-price and "Prev close" issues were
found by the owner exercising the shipped feature against their own portfolio data (PEPE) and
fixed in the same session.
