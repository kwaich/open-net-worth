# ADR 0007 — Asset-detail navigation: a shared AssetNav context + tappable `<AssetLink>`

- Status: Accepted
- Date: 2026-06-19
- Extends: ADR 0003 (watchlist collections), ADR 0004 (deterministic signals/citations)

## Context

Every Intel surface names assets — watchlist rows, signal/brief cards, story arcs, peers, digest
ticker chips — but **none of them were tappable**, so there was no way to get from Intel to an
asset-detail page. The single existing entry point (`openAssetDetail`) lived in `App.tsx` and was
prop-drilled to a couple of places; wiring it into every ticker surface would have meant threading
the same callback through a dozen components, and each one re-implementing the "I only have a ticker
string, not an instrument_id" case differently.

Two shapes of caller exist:

- **Has an `instrument_id`** (watchlist rows already carry one) → can open directly.
- **Has only a ticker string** (peer names, digest chips, story arcs) → must resolve the symbol to an
  instrument first, *creating* the instrument record if it is not yet known (find-or-create).

## Decisions

1. **One React context, not prop-drilling.** `state/assetNav.tsx` exposes `AssetNavProvider` +
   `useAssetNav() → openAsset({instrumentId?, ticker?, label?, currency?})`. `App.tsx` wraps the Intel
   subtree once, passing its existing `openAssetDetail` to the provider. Any descendant opens an asset
   without the callback being threaded through intermediates. This mirrors the existing
   `chatComposer`/mention-staging context pattern (a cross-cutting UI capability provided once at a
   subtree root, consumed by leaves) rather than inventing a new convention.

2. **`openAsset` owns the resolve round-trip.** If `instrumentId` is present it delegates straight to
   `openAssetDetail`. If only `ticker` is given it calls the resolve endpoint, then opens with the
   returned id. Callers never branch on "do I have an id"; the context centralizes it. On failure it
   **no-ops and `console.warn`s — never throws** (a dead ticker must not break the page).

3. **Resolve is `POST /api/intel/assets/resolve`, not GET.** The endpoint is find-or-create: it writes
   an `instruments.json` record when the ticker is unknown. A GET with a write side-effect violates
   RFC 9110 §9.2.1 (safe/idempotent) and would expose the write to browser prefetch and caching
   proxies. So the contract is `POST {q: ticker} → {instrument_id, label, ticker, currency}` or 404.

4. **In-flight dedup by ticker.** `openAsset` tracks pending resolves in a `useRef<Set<string>>`;
   a second click on the same ticker while its resolve is pending is dropped, so a rapid double-tap
   can't double-create or double-navigate.

5. **One reusable primitive: `<AssetLink>`.** `components/intel/AssetLink.tsx` renders a real
   `<button>` (keyboard-focusable, announced) when navigable, and **degrades to a plain `<span>`** when
   neither `instrumentId` nor `ticker` is available — so a name with no resolvable symbol simply isn't
   interactive, no dead affordance. Every ticker surface uses this one component; adding navigation to a
   new surface is `<AssetLink ticker=… />`, never a bespoke handler. Callers must keep the surrounding
   DOM non-interactive (no button-in-button).

## Consequences

- New ticker surfaces get navigation for free and consistently; no per-surface resolve logic.
- The write side-effect is honest (POST), cache-safe, and deduped.
- `AssetLink` must not be nested inside another interactive element — enforced by convention + the a11y
  note in the component header, not by code.
- The resolve endpoint can create instruments from arbitrary ticker strings; it reuses the existing
  `services.instruments.resolve_instrument` find-or-create (same path the watchlist add already trusts),
  so no new write surface is introduced.
