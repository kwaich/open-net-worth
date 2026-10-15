# ADR 0013 — Omnibox command palette: market search + Ask Lucius

- Status: Accepted
- Date: 2026-06-20
- Extends: ADR 0007 (asset-nav context + `<AssetLink>`), ADR 0012 (TanStack Router + hash history)

## Context

The Command+K palette only searched **in-app** objects (tabs, accounts, holdings,
categories). Two gaps followed:

1. A user could only open the AssetDetail view for assets they **already hold**. To
   research a stock or coin they don't hold, their only path was to ask Lucius in chat —
   there was no way to type "apple" or "btc" and land on that asset's detail screen.
2. A long natural-language query ("how much did I spend on coffee in May") produced
   useless fuzzy matches and had nowhere to go.

The render path for a non-held asset already existed: `/asset/$instrumentId` renders
`AssetDetailView` with a nullable `position` (ADR 0007), and `resolve_instrument` is a
find-or-create that works for any ticker. Only a **discovery entry point** was missing.

## Decisions

1. **Extend Command+K into an omnibox — not a new search surface.** The palette gains two
   tiers below the existing in-app groups: **Markets** (remote symbol search for non-held
   assets) and **Ask Lucius** (a catch-all that seeds the raw query into chat). One
   universal input, consistent with the existing palette, rather than a second search box.

2. **A read-only list endpoint, separate from find-or-create.** `GET /api/intel/assets/
   search?q=&limit=` returns a ranked **candidate list** (`search_market` in
   `services/prices.py`, aggregating Yahoo equities/ETFs + CoinGecko coins + the fixed
   metals map). It is deliberately **read-only and creates nothing** — instrument creation
   stays lazy, on select, via the existing `POST /api/intel/assets/resolve`. This keeps the
   typeahead safe to call on every debounced keystroke and preserves the ADR 0007 rule that
   the only write path is the POST. (`search_symbol`/`search_coin` already existed but
   returned a single best match — the list is the new capability.)

3. **Candidates carry the canonical pricing key; resolve takes hints.** Each candidate
   includes `price_source` + `price_source_id`. On select, the palette passes these (plus
   `asset_class` and the human-readable `name`) to `resolve`, which forwards them to
   `resolve_instrument`'s `(price_source, price_source_id)` fast path — a **direct
   find-or-create on the canonical key, with no second provider lookup**. This is the
   correctness crux: without it, a query like "SOL" resolves Yahoo-first and can land on a
   look-alike ETP instead of the CoinGecko coin. The `name` hint is threaded through so the
   created instrument's label reads "Bitcoin", not "BTC".

4. **Ask Lucius is always present with a smart default.** It renders for any non-empty
   query and takes the default Enter highlight only when the query is question-shaped
   (`looksLikeQuestion`: ends with `?`, ≥4 words, or a leading question word) **or** there
   is no strong structured match; otherwise the best structured match keeps Enter and Ask
   Lucius is a pinned escape-hatch at the bottom. Selecting it **seeds, never auto-sends** —
   the LLM never fires from a keystroke; it reuses the existing chat prefill path and the
   `requestOpen(fresh)` bridge for a new thread.

5. **The palette resolves tickers via the shared `openAssetDetail`, not `useAssetNav`.**
   The palette is mounted outside `AssetNavProvider` (which wraps only the Intel view), so
   it cannot consume the context. Rather than widen the provider, `openAssetDetail` in
   `RouterShell` was extended to handle the ticker-only case (resolve → navigate, in-flight
   deduped, fail-soft `console.warn`), mirroring `assetNav`'s contract. `assetNav` is left
   untouched.

## Consequences

- Any stock/coin/metal is now reachable from one keystroke surface; non-held assets reuse
  the existing AssetDetail render path with no new screen.
- Cost/rate-limit on providers is bounded by: a 250ms debounce + `AbortController` (drop
  stale) + a per-query client cache + a 60s server TTL cache + a `limit` clamp (1–20).
- Provider responses are treated as untrusted data and never executed; the new GET creates
  nothing, so the only write surface remains the pre-existing POST.
- Two code paths now resolve tickers (`assetNav.openAsset` for Intel surfaces,
  `openAssetDetail` for the palette). They share the same endpoint and fail-soft contract;
  consolidating them is possible later but not required.
- Deferred (v1 YAGNI): recent-search history, inline price/sparkline on rows, results on an
  empty query, auto-send to Lucius, and metals beyond the fixed four.
