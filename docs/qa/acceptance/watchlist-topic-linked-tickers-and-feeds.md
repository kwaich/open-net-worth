# Acceptance — Topic & ticker "Advanced: news sources" (linked tickers + custom feeds) [INT10]

Both the Topic editor (`TopicDetailModal.tsx`) and the Ticker editor (`PriceAlertsModal.tsx`)
gain a collapsed-by-default "Advanced: news sources" section wrapping the two INT5/INT9 fields
already on the watchlist-item write choke: `linked_tickers` (topic only, cap 10) and
`custom_feed_urls` (both, cap 5). These criteria are the QA contract for that UI.

## NSRC-1 — Topic modal bumped to lg; section collapsed by default, no badge when empty
- **GIVEN** a user opens the Topic detail modal for any topic
- **WHEN** it renders
- **THEN** the modal card is size `lg` (was `md`)
- **AND** an "Advanced: news sources" section is present, collapsed by default
- **AND** no count badge shows while the topic has 0 linked tickers and 0 custom feeds

## NSRC-2 — Live count badge, halves dropped when empty
- **GIVEN** a topic already has 2 linked tickers and 1 custom feed
- **WHEN** the modal opens and the section is expanded
- **THEN** the header badge reads "2 linked · 1 feed"
- **AND** adding/removing a ticker or feed updates the badge live, before Save

## NSRC-3 — Linked tickers: add via TagInput, Enter adds a chip
- **GIVEN** the Advanced section is expanded on a topic
- **WHEN** the user types a ticker and presses Enter
- **THEN** a chip is added under "Linked tickers"
- **AND** the help text reads "Get this topic's news from these tickers' fast feeds — minutes
  instead of hours. Up to 10."

## NSRC-4 — Linked tickers cap at 10
- **GIVEN** a topic already has 10 linked tickers
- **WHEN** the user types an 11th and presses Enter
- **THEN** the add is blocked and the typed text remains in the field
- **AND** the help text swaps to the muted "Maximum 10 tickers." (NOT error tone)

## NSRC-5 — Paste splits a multi-ticker block into chips
- **GIVEN** the Linked tickers field is focused and empty
- **WHEN** the user pastes "AAPL, MSFT NVDA"
- **THEN** three chips are added: AAPL, MSFT, NVDA
- **AND** a single-token paste (e.g. "AAPL") does NOT auto-add — it lands in the draft as normal
  text, regression-safe for any other TagInput consumer (e.g. transaction tags)

## NSRC-6 — Custom feeds: empty state, add/remove rows
- **GIVEN** an item has no custom feeds
- **WHEN** the Advanced section is expanded
- **THEN** "No custom feeds yet." is shown
- **AND** clicking "+ Add feed URL" adds an empty row (full-width TextField + remove button)
- **AND** clicking a row's remove button deletes that row

## NSRC-7 — Custom feeds cap at 5
- **GIVEN** 5 feed rows already exist
- **WHEN** the section is viewed
- **THEN** "+ Add feed URL" is replaced by the muted "Maximum 5 feeds." text (no further rows)

## NSRC-8 — Feed-row paste splits into rows up to the cap
- **GIVEN** one feed row is focused
- **WHEN** the user pastes a multi-URL block
- **THEN** each URL lands in its own row, up to the 5-row cap
- **AND** extra URLs beyond the cap are silently dropped (the cap notice covers it)

## NSRC-9 — Enter in a feed URL field never submits the modal
- **GIVEN** a feed row TextField is focused
- **WHEN** the user presses Enter
- **THEN** the modal does NOT save/close — the keypress is swallowed

## NSRC-10 — Save extends the single existing PATCH (topic)
- **GIVEN** a topic has edits to name/note/mute/linked-tickers/custom-feeds
- **WHEN** the user clicks Save
- **THEN** exactly ONE `PATCH /api/watchlist/items/{id}` fires, whose body includes
  `linked_tickers` and `custom_feed_urls` (blank feed rows filtered) alongside the existing fields

## NSRC-11 — Save extends the mute PATCH (ticker); no second network call
- **GIVEN** a ticker watchlist item (`item.id` truthy) has custom-feed edits
- **WHEN** the user clicks Save in the Price Alerts modal
- **THEN** the existing alerts DELETE/POST reconcile is unchanged
- **AND** exactly ONE PATCH to `/api/watchlist/items/{id}` fires carrying both `monitor_news` and
  `custom_feed_urls` — no separate feeds-only request

## NSRC-12 — 422 on `linked_tickers` routes inline
- **GIVEN** the backend rejects the topic PATCH with a 422 `linked_tickers: ...` detail
- **WHEN** Save is clicked
- **THEN** the Advanced section force-opens, the Linked tickers field shows the verbatim message
  as its error, and the modal stays open with every value intact (no generic banner)

## NSRC-13 — 422 on `custom_feed_urls` routes inline + bad-row highlight; non-field failure banner
- **GIVEN** the backend rejects a PATCH (topic or ticker) with a 422
  `custom_feed_urls: '<url>' is not a safe http(s) URL (...)` detail
- **WHEN** Save is clicked
- **THEN** the Advanced section force-opens, Custom news feeds shows the message verbatim, and
  the TextField row whose value matches the quoted URL gets a red border + `aria-invalid`
- **AND** for a non-field failure (network/500) the generic "Could not save — please try again."
  banner shows instead (reusing `.intel-wlc-add-err`), and the modal stays open with edits intact

## NSRC-14 — Ticker editor is feeds-only; hidden for a synthetic standalone-alert row
- **GIVEN** the Price Alerts modal for a real watchlist ticker item
- **WHEN** the Advanced section is expanded
- **THEN** only "Custom news feeds" appears — no "Linked tickers" field
- **AND** for a synthetic standalone-alert row (`id: ''`, no backing watchlist item) the entire
  Advanced section is hidden, mirroring the existing mute-row guard

## Verification status
- Backend: `db/watchlist_store.py` validation (cap + SSRF-guard messages) pre-dates this lane
  (INT5/INT9) and is unchanged; `asset_detail._watchlist_block` now also surfaces
  `custom_feed_urls` for the asset-detail rail call site (`tests/test_asset_detail_service.py`,
  `tests/test_intel_assets_api.py` — subset-of-keys assertions, unaffected).
- Frontend (vitest): `TagInput.test.tsx` (Enter/comma/Backspace regression + paste-split + max
  cap — 8 tests), `TopicDetailModal.test.tsx` (layout/badge/linked-tickers/feeds/save/422/generic
  — 9 tests), `PriceAlertsModal.test.tsx` (feeds-only/synthetic-row-hidden/save/422 — 4 tests),
  `AssetAlertsCard.test.tsx` (unaffected — 6 tests, still green). Full suite: 2046/2046 passing.
  `tsc` clean (only 38 pre-existing baseline errors, unchanged before/after, none in touched
  files), `lint:classes` clean, `npm run build` clean.
- Remaining for the full UI Quality Gate: `make qa-e2e` for NSRC-1/2/5/8/10/12/13/14, `make
  ui-gate`, and screenshot evidence (collapsed, expanded, both 422 states, mobile ≤640 reflow —
  chips wrap, feed rows go single-column via the existing modal reflow, no new CSS needed).
