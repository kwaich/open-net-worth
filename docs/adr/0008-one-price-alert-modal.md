# ADR 0008 — One price-alert modal: consolidate onto the rich `PriceAlertsModal`

- Status: Accepted
- Date: 2026-06-19
- Extends: ADR 0003 (watchlist collections), ADR 0007 (asset-nav context)

## Context

Two separate modals edited the **same** thing — a watchlist ticker's `price_alerts` array,
saved via `PATCH /api/watchlist/items/{id}`:

- `PriceAlertsModal` (rich, `size="lg"`) on the Intel watchlist / Monitors: mute toggle, a
  multi-line "Instructions for Lucius" per threshold with Draft/Rewrite, "Suggest alerts", and
  "Remove from watchlist".
- `PriceAlertEditor` (lean, `size="md"`) on the asset-detail right-rail card: op + threshold + a
  single-line note. No mute, no instructions, no suggestions.

The lean editor was a strict **subset** of the rich one and had drifted — its own comment even
called it "the shared PriceAlertEditor" though it was the un-shared, lesser variant. The asset-detail
surface got a worse alert experience for no reason other than three soft dependencies the rich modal
carried that the page didn't thread through:

1. **`luciusCtx`** (holdings-aware engine context) was built inline in `Intel.tsx` and prop-drilled.
2. The two reference tables it needs — `DEFAULT_TARGETS` (target allocation %) and `CLASS_LABELS`
   (asset-class display names) — were **module-private to `Intel.tsx`**, so only that page could
   assemble a context.
3. Mute (`monitor_news`) and "Remove from watchlist" had surface-specific semantics.

## Decisions

1. **One modal.** Delete `PriceAlertEditor`; both surfaces render `PriceAlertsModal`. Its CSS
   (`intel-alert-editor-*`) is removed; `intel-pa-*` is the single alert-modal namespace.

2. **The modal self-builds its context; the prop becomes an optional override.** A new
   `hooks/useLuciusContext.ts` derives `LuciusContext` from `usePortfolio()` + the now-shared
   constants. `Intel.tsx` keeps passing its already-memoized context; asset-detail omits the prop and
   the modal builds its own. This is why the answer to "why must the context be passed?" is "it
   needn't be" — it was page-derived data, not a page-specific dependency. To avoid a redundant
   `usePortfolio` (live fetch + 60s poll) when the caller *does* pass a context, `PriceAlertsModal` is
   a thin dispatcher: it renders an inner `SelfContextAlertsModal` (which calls `useLuciusContext`)
   **only** when no `luciusCtx` is given, so the hook never mounts on the Intel path.

3. **Constants move to the engine module.** `DEFAULT_TARGETS` / `CLASS_LABELS` are now exported from
   `utils/luciusEngine.ts` (the pure module that already owns the types and the suggestion logic);
   `Intel.tsx` imports them instead of defining them. Single source of truth for the allocation model.

4. **The `item` prop is decoupled from any one caller's type.** The modal now takes a minimal
   structural `AlertModalItem` (`id, value, label, asset_class, price_alerts, monitor_news`) — the
   exact fields it reads — so it does not couple to `WatchlistsSection`'s fuller `WatchlistItem`. The
   Intel watchlist passes its full item (structurally compatible); asset-detail builds the small one.

5. **`monitor_news` is added to the asset-detail payload.** `services/asset_detail._watchlist_block`
   now returns `monitor_news` (default `True`) and the frontend `WatchlistStatus` type carries it, so
   the mute toggle reflects reality on asset-detail instead of always defaulting to "Alerts on".

6. **"Remove from watchlist" is hidden on asset-detail** via `canRemove={false}`; `onRemove` is now an
   optional prop guarded by `canRemove && onRemove`. Removal on that surface is the header Follow
   toggle; the card auto-follows on first alert (unchanged).

7. **Strict ops round-trip losslessly.** The rich modal's operator Select offers two choices
   (`≥ at or above` / `≤ at or below`), so a stored `>`/`<` (only reachable via the AI tool or raw API)
   is folded for display. Each row keeps its `origOp`; an *untouched* row emits its original op on save,
   so opening and saving an item never silently narrows a strict alert to at-or — the op is only
   rewritten when the user actually changes that row's operator.

## Consequences

- Asset-detail gains mute + Instructions-for-Lucius + Suggest with no bespoke code; one editor to
  maintain, one place for alert UX to evolve.
- `usePortfolio` mounts at most once per open modal: the Intel path reuses the page context (no extra
  fetch/poll); the asset-detail path self-builds. Unit tests that mount the modal stub
  `useLuciusContext` for determinism.
- `monitor_news` is now part of the asset-detail contract (regression-tested in
  `test_asset_detail_service.py`); the mute round-trip and strict-op preservation are regression-tested
  in `AssetAlertsCard.test.tsx`.
