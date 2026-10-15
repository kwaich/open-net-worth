# 0014 — Decouple price alerts into a standalone, ticker-keyed store

| | |
|---|---|
| **Status** | Accepted |
| **Date** | 2026-06-20 |
| **Supersedes** | ADR 0008 (§ save-path only — the alert *modal* consolidation stands; the *storage/endpoint* moves) |
| **Relates to** | ADR 0009 (watchlist membership consolidation), ADR 0006 (Intel Analyst pipeline) |

## Context

Price alerts (`{op, value, note}` thresholds — "ping me when AAPL ≤ $150") were stored as a
`price_alerts: []` array **embedded inside each watchlist item** (`watchlist.json → items[]`).
A watchlist item is one-per-ticker with a `watchlist_ids[]` membership array (ADR 0009), so alerts
were already de-facto per-ticker — but they *rode on* watchlist membership. Three problems followed:

1. **Silent data loss.** Removing a ticker from its **last** watchlist deletes the item and its
   embedded alerts (`WatchlistStore.remove_from_watchlist`). The new asset-detail `AssetWatchlistMenu`
   makes this a one-click action sitting directly beside the alerts it silently destroys.
2. **"My holdings" pollution.** Setting an alert auto-creates a watchlist item in the `default`
   ("My holdings") watchlist (`AssetAlertsCard` auto-follow → `POST /api/watchlist/items` with no
   `watchlist` → backend defaults to `default`). An alert on a *non-owned* ticker forces it into a
   list that advertises itself as "every position you own."
3. **Conceptual coupling.** The UI already presents watchlists ("what I track") and price alerts
   ("what fires") as orthogonal (separate asset-detail controls, a dedicated `PriceAlertsModal`, a
   `Price alerts` subtab in the monitors workspace). Only the storage lagged. As a side effect,
   `AllMonitorsView` groups alerts strictly by watchlist, so an alert on a ticker in **no** watchlist
   is *counted* but never *shown*.

## Decision

Make price alerts a first-class, **ticker-keyed** concept stored independently of watchlists.

- New `alerts.json` + `AlertStore` (`JsonStore`, flat list, one row per condition). An alert:
  `{ id, ticker, instrument_id?, op, value, note, label, asset_class, created_at }`.
- A single service choke point `services.alerts.apply_alert_change({type: create|update|delete, …})`.
- A thin `api/alerts.py` router: `GET/POST/PATCH/DELETE /api/alerts`.
- AI tools `get_alerts` (headless) / `set_price_alert` / `update_price_alert` / `remove_price_alert`.
- The `price_monitor` **alert lane reads `AlertStore`** instead of watchlist items — mirroring the
  **crash lane**, which already iterates holdings (the source of truth), not the watchlist.
- A **one-time migration** lifts every embedded `price_alerts` entry into `AlertStore` (keyed by the
  item's ticker) and strips the field from items permanently. No back-compat read shim.
- Frontend rewires `AssetAlertsCard` + `PriceAlertsModal` to `/api/alerts`; setting an alert no longer
  auto-creates a watchlist item; `AllMonitorsView`'s `Price alerts` subtab becomes a **flat list of all
  alerts** with watchlist-membership chips (so no-watchlist alerts are visible — the count/list
  mismatch is structurally fixed). `AssetWatchlistMenu` removal can never touch alerts.

### Sub-decisions (the load-bearing ones)

- **Mute stays on the watchlist item (`monitor_news`), NOT on the alert.** The mute toggle is a
  *per-ticker* "silence without losing alerts" switch that already round-trips through `item.monitor_news`.
  A per-threshold mute is a new product surface nobody built. The alert lane gains a per-ticker mute
  check (look up the ticker's `monitor_news`). **Consequence:** a standalone alert on a ticker with no
  watchlist item cannot be muted — acceptable (delete it instead). A per-alert mute is a clean follow-up.
- **Armed / rising-edge state stays in `MONITORING_PRICE_CACHE_JSON` (checkpoint-EXCLUDED), keyed by
  `(ticker, "{op}{value}")` — untouched by the migration.** Moving it onto the alert record (a new
  `uuid`) would orphan every existing cache entry and re-fire every already-crossed alert once = a spam
  storm. Keeping the key identical means a crossed-and-armed alert keeps its armed state across the
  decouple (no re-fire), and an uncrossed alert still fires correctly on first crossing. **`value` is
  coerced to `float` at every entry point** (store, API, migration) so the cache key string is stable
  (`">=150.0"`, never `">=150"`).
- **The firing path is UNCHANGED.** `price_monitor` still upserts an `IntelSignalsStore` signal with
  `id=f"price-alert-{ticker.lower()}-{safe_key}"`, `type="price_alert"`, and pushes to `alert_channels`.
  Only the *source of conditions* moves. The Intel main feed, digest, and `get_signals` consume the
  signal, not the source — so they are untouched.
- **`alerts.json` is checkpoint-INCLUDED and portable** (durable user money-config, like `watchlist.json`).
  Flat list → not in `_DICT_ROOT_FILENAMES` (empty seed `[]`). Armed-state remains the only alert-related
  runtime cache and stays checkpoint-EXCLUDED.

### Migration safety (one-time, idempotent, crash-safe)

- **Trigger:** runs at startup; the real "needs migration" signal is **"any item still carries a
  non-empty `price_alerts`"** — not merely "`alerts.json` is absent." This survives a crash between the
  alert-write and the item-strip.
- **Algorithm (lock-held):** lift each condition (accepting the dead legacy `{direction,target,triggered}`
  example shape too) into `AlertStore`, deduped by `(ticker, op, value)`; then strip `price_alerts` from
  every item via the store's atomic writer. Iterates **all** item types (not only `type=="ticker"`).
  Per-condition `try/except` with a warning log — a single malformed row can never crash the boot loop.

## Consequences

- Removing a ticker from a watchlist never deletes its alerts. Setting an alert never pollutes "My
  holdings." Alerts on non-watchlisted tickers are valid and visible.
- `WatchlistItem` loses `price_alerts` (backend + TS type optional-then-removed; all readers source from
  `AlertStore`). `has_price_alerts` filter removed. `PATCH /api/watchlist/items` no longer accepts
  `price_alerts`.
- `Price alerts (N)` in `AllMonitorsView` counts alert rows (thresholds), a small intentional count shift
  from "tickers-with-alerts."
- One new CSS token (`intel-mm-wl-chip`) added to `index.css` (CSS-class discipline).
- Layering respected: `AlertStore` (db) imports only `core`; `services.alerts` imports `db`; `api/alerts`
  imports `services`; `ai/tools/alerts` imports `services` via the `alert_store()` factory; `monitoring`
  is layer-unconstrained but reads through `services.alerts` for cleanliness.

## Alternatives considered

- **Confirmation guardrail only** (warn before deleting alerts on last-watchlist removal): fixes the
  *silent* part of the data loss but not the conceptual coupling or the pollution. Rejected as a band-aid.
- **Keep storage, change removal semantics** (keep an "alert-only" item with empty `watchlist_ids`):
  trades one modeling smell (alerts ride watchlists) for another (orphan items). Rejected.
- **Per-alert mute / armed-state on the record:** rejected for this change (mute = new surface; armed-on-
  record = re-fire storm). Both are clean follow-ups if needed.

## Agent provenance

Designed via a collaborative brainstorm (the coupling, the Intel-feed visibility question, the
"My holdings" pollution trace) then an 8-agent discovery+plan+adversarial-critique workflow that mapped
every `price_alerts` touchpoint and surfaced the 5 migration/data-loss blockers folded in above.
