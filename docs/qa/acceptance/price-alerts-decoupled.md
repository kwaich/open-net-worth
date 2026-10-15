# Acceptance — Price alerts decoupled from watchlists (ADR 0014)

Price alerts are a standalone, ticker-keyed concept (`alerts.json` / `/api/alerts`), independent of
watchlist membership. These criteria are the QA contract for the decouple.

## PAD-1 — Removing a ticker from its last watchlist keeps its alerts
- **GIVEN** ticker AAPL is on exactly one watchlist and has 2 price alerts
- **WHEN** the user removes AAPL from that watchlist (sidebar, AssetWatchlistMenu, or `remove_watchlist_item`)
- **THEN** the watchlist item is deleted **but the 2 alerts survive** (still returned by `GET /api/alerts?ticker=AAPL`)
- **AND** the removal confirmation copy does NOT claim the alerts will be deleted

## PAD-2 — Setting an alert does not create a watchlist item
- **GIVEN** ticker NVDA is on NO watchlist
- **WHEN** the user sets a price alert on NVDA from the asset-detail Price Alerts card
- **THEN** an alert row is created in the store (`POST /api/alerts`)
- **AND** NVDA is **not** added to any watchlist (no pollution of "My holdings")

## PAD-3 — Standalone alerts are visible in All monitors
- **GIVEN** an alert exists on a ticker that is on no watchlist
- **WHEN** the user opens Watchlists & monitors → All monitors → Price alerts
- **THEN** the ticker + its alert chips are listed (NOT silently dropped)
- **AND** the row shows a "not in any watchlist" affordance
- **AND** the "Price alerts (N)" count equals the number of alert rows (thresholds), not items

## PAD-4 — Membership chips on alerts that are on watchlists
- **GIVEN** an alert on a ticker that belongs to watchlists "AI" and "Themes"
- **WHEN** the user views it in the flat Price alerts list
- **THEN** the row shows membership chips "AI" and "Themes"

## PAD-5 — Editing alerts writes to /api/alerts, one request per control
- **GIVEN** a ticker with alerts ≤ $100 and ≥ $200
- **WHEN** the user edits ≥ $200 to ≥ $210 (Save alert) and deletes ≤ $100 (Delete alert) in the Price Alerts modal
- **THEN** the store reflects exactly { ≥ $210 } for that ticker — a `PATCH /api/alerts/{id}` and a `DELETE /api/alerts/{id}`, never a watchlist PATCH and never a delete-all-then-recreate (the surviving alert keeps its id and armed state; see `price-alerts-redesign.md`)
- **AND** strict ops `>` and `<` round-trip losslessly through an unedited Save

## PAD-6 — Mute stays per-ticker on the watchlist item
- **GIVEN** a ticker that IS on a watchlist, with alerts
- **WHEN** the user toggles "Mute <TICKER>" in the modal (it commits itself — there is no Save)
- **THEN** the item's `monitor_news` is PATCHed (no per-alert mute), and the muted ticker's alerts do not fire
- **AND** for a ticker on NO watchlist (no item), the mute toggle is hidden (delete the alert instead)

## PAD-7 — Migration lifts embedded alerts once, crash-safe
- **GIVEN** a legacy `watchlist.json` whose items carry embedded `price_alerts`
- **WHEN** the backend boots
- **THEN** every embedded alert is lifted into `alerts.json` (keyed by ticker, instructions preserved) and the
  field is stripped from items
- **AND** the migration is idempotent (a second boot is a no-op) and never double-books on a partial re-run

## PAD-8 — Firing path unchanged
- **GIVEN** a price alert whose threshold is crossed
- **WHEN** the price monitor runs
- **THEN** it upserts a `type="price_alert"` IntelSignals story (same `price-alert-<ticker>-<key>` id) and pushes
  to the configured `alert_channels` — exactly as before the decouple
- **AND** an already-armed threshold does NOT re-fire across the migration (no spam)

## Verification status
- Backend: AlertStore CRUD, service, `/api/alerts` smoke, migration (idempotent/crash-safe/malformed-skip),
  monitor-lane armed-state + mute regressions — all green (`pytest -m "not integration"`).
- Frontend: AssetAlertsCard, WatchlistsSection, MonitorsWorkspace flat-list tests — green (vitest);
  `tsc`, `lint:classes`, `build` clean.
- Remaining for full UI Quality Gate: `make qa-e2e` for PAD-1/PAD-3/PAD-5 + `make ui-gate` + screenshot evidence.
