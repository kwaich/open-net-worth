# Acceptance criteria — Intel tab (redesign)

Interaction-level GIVEN/WHEN/THEN for the redesigned **Intel** tab: the portfolio Brief card,
multiple Watchlists, the restyled signal feed (incl. Risk signals + feed citations), and the
navigation fix. The QA agent must exercise every criterion and report PASS/FAIL per ID. Supersedes
the pre-redesign Intel contract.

> **Scope note (2026-06-17):** the **Asset detail page is intentionally kept as-is** (its redesign
> was descoped at the owner's request — see `.claude/rfcs/intel-redesign.md` "Scope amendment").
> The asset-detail-redesign criteria (a top Lucius briefing block, 3-lens tabs, key-issues-as-tabs,
> a graded freshness chip on the asset header) are therefore **DEFERRED**, not part of this contract.
> Asset detail still opens, renders, and the **back button returns to the source tab** (see NAV-*).

---

## Load + brief

### INTEL-LOAD
- GIVEN I am on any tab
- WHEN I click "Intel" in the sidebar
- THEN the Intel tab loads showing a page header ("Intelligence") with freshness chips and a
  "Scan now" button, the "Your Intel Brief" card, the Watchlists section, and either a
  signal feed or a cold-start empty state — with no JS error and no blank screen.

### INTEL-BRIEF-CARD
- GIVEN I am on the Intel tab with holdings
- WHEN the page loads
- THEN the "Your Intel Brief" card shows three sections — Market backdrop, Return drivers, and
  Top movers — populated from my portfolio (not placeholder text), each return-driver/mover row
  showing a ticker and a base-currency figure.

### INTEL-BRIEF-EMPTY
- GIVEN a fresh instance with no holdings and no digest
- WHEN I view the Intel tab
- THEN the brief card shows its empty state ("No brief yet" / prompts to scan), NOT a blank box or an error.

### INTEL-BRIEF-DRIVER-NAV
- GIVEN the brief card shows a return-driver or top-mover row
- WHEN I click that row
- THEN I am taken to that asset's detail page (the Asset detail view for that instrument opens).

---

## Scan

### INTEL-SCAN-COST-CONFIRM
- GIVEN I am on the Intel tab
- WHEN I click "Scan now"
- THEN a confirmation modal opens explaining the scan makes external web-search API calls and
  consumes quota — the scan does NOT start immediately without this dialog.

### INTEL-SCAN-CONFIRM-EXECUTES
- GIVEN the scan cost confirmation modal is open
- WHEN I click "Scan now" in the modal
- THEN the modal closes, the button shows a "Scanning…" state, and after a moment the feed
  (or cold-start state) refreshes.

---

## Signal feed + Risk lane + citations

### INTEL-FEED-STATUS-GROUPS
- GIVEN the Intel tab has signals
- WHEN I view the feed
- THEN signals are grouped into labeled bands — "Needs attention" (red), "Active" (orange),
  "Monitoring" (grey) — not a flat unsorted list.

### INTEL-FILTER-RISK
- GIVEN the feed has signals of multiple types and the filter bar is visible
- WHEN I click "Risk" in the filter SegmentedControl
- THEN only Risk-type signals are shown; signals of other types disappear. (The filter bar
  includes Risk alongside Opportunity / Macro / Asset / Topic.)

### INTEL-RISK-CARD
- GIVEN my portfolio has a concentrated position (one holding > the risk threshold for my risk
  tolerance) and a scan has run
- WHEN I view the feed (or filter to Risk)
- THEN a Risk card appears naming the concentration (or other risk: FX mismatch / sector
  overweight / drawdown) with a portfolio-impact line and an action item — generated without an
  external search call.

### INTEL-CITATION-CHIP
- GIVEN a signal card (or asset briefing) with cited sources
- WHEN I view its summary
- THEN each cited claim shows a citation chip (favicon + source + date); hovering the chip reveals
  the pulled quote; clicking it opens the source in a new tab. Chips are NOT bare "[1][2]" links.

### INTEL-DISMISS-SIGNAL
- GIVEN a signal card with a Dismiss button
- WHEN I click Dismiss
- THEN the card is removed from the feed immediately — no page reload.

### INTEL-COLDSTART-EMPTY
- GIVEN no signals exist yet
- WHEN I view the Intel tab
- THEN the cold-start empty state is shown with starter topic chips and a "Scan now" button —
  NOT a blank page, NOT an error.

---

## Watchlists (multiple named lists)

### WL-DEFAULT-LIST
- GIVEN I open the Watchlists section
- WHEN it loads
- THEN a list switcher shows at least the default "Watchlist" with its item count, and the
  Assets / Topics sub-tabs render below it.

### WL-CREATE-LIST
- GIVEN the Watchlists section is open
- WHEN I click "+ New list", type a name (e.g. "High conviction"), and confirm
- THEN the new list is created and becomes selectable in the switcher (and selected) — no reload.

### WL-SWITCH-LIST
- GIVEN I have more than one watchlist
- WHEN I open the switcher and pick a different list
- THEN the asset/topic rows shown update to that list's items.

### WL-RENAME-LIST
- GIVEN a non-default list is selected
- WHEN I rename it via the switcher and confirm
- THEN the switcher shows the new name immediately.

### WL-DELETE-LIST
- GIVEN a non-default list is selected
- WHEN I delete it and confirm
- THEN the list disappears from the switcher, its items are reassigned to the default list (not
  lost), and the default cannot itself be deleted (no delete affordance, or it is refused).

### WL-ADD-TICKER-TO-LIST
- GIVEN a list is selected and the Assets sub-tab is active
- WHEN I type a ticker (e.g. "NVDA") and click Add (or press Enter)
- THEN the ticker appears as a row in THAT list, with its live price populated,
  NOT in a different list and not an error.

### WL-REMOVE-TICKER
- GIVEN at least one ticker is in the active list
- WHEN I click the "×" on its row
- THEN the row is removed immediately (no reload) and the row click-through did NOT fire.

### WL-ROW-NAV
- GIVEN a ticker row is visible in a list
- WHEN I click the row (not the × or Edit)
- THEN that asset's detail page opens.

### WL-PRICE-ALERT-SAVE
- GIVEN a watched ticker row with an "Edit alerts" affordance
- WHEN I open the alert editor, add a threshold (e.g. ≥ 150), and Save
- THEN the editor closes and the condition chip (e.g. "≥ 150") appears on that row.

### WL-MONITORING-SETTINGS-SAVE
- GIVEN the Monitoring settings panel is visible
- WHEN I change the "Price check interval"
- THEN it saves automatically (PATCH /api/watchlist/config) and a "Saved" flash appears — no
  separate Save button.

### WL-MON-RSS-HELP-COPY
- GIVEN the Monitoring settings panel is visible
- WHEN I read the "RSS freshness" field's help text
- THEN it reads exactly "Sets the fast-lane cadence (holdings + topics) and keeps the ~90s
  breaking-news lane running. Off stops both — no fast polling, no breaking-news lane, only the
  slow ~30 min backfill." — the literal "~90s" is hardcoded, never rendered as "2m".

### WL-MON-BREAKING-ROW-ACTIVE
- GIVEN Monitoring is active and "RSS freshness" is NOT "Off"
- WHEN I view the new "Breaking news lane" row (immediately after RSS freshness, before Web
  search)
- THEN it reads "Breaking news lane · Active · ~90s" with the status dot in its `--on` (lit)
  state, and the "Outlet headlines" switch is enabled.

### WL-MON-BREAKING-ROW-OFF
- GIVEN Monitoring is active and "RSS freshness" is set to "Off"
- WHEN I view the "Breaking news lane" row
- THEN it reads "Breaking news lane · Off · ~90s", muted (no `--on` modifier), and the "Outlet
  headlines" switch is disabled with a tooltip "Turn RSS freshness on to enable outlet headlines".

### WL-MON-BREAKING-ROW-MONITORING-PAUSED
- GIVEN "Monitoring active" is toggled off
- WHEN I view the "Breaking news lane" row
- THEN it still reads "Breaking news lane · Off · ~90s" (never hidden) and the "Outlet headlines"
  switch is disabled.

### WL-MON-OUTLET-TOGGLE-ROUNDTRIP
- GIVEN Monitoring is active and "RSS freshness" is NOT "Off" (Outlet headlines enabled)
- WHEN I toggle "Outlet headlines" off
- THEN a PATCH /api/watchlist/config fires with `{"outlet_feeds_enabled": false}`, the toggle
  stays off after a reload, and toggling it back on PATCHes `{"outlet_feeds_enabled": true}` and
  persists on reload.

### WL-MON-BREAKING-ROW-NO-CLUTTER-HEADER
- GIVEN the Monitoring panel is collapsed
- WHEN I read the collapsed header's cadence label
- THEN it still reads exactly "Alerts X · News Y" (active) or "Paused" (inactive) — no third
  breaking-news token was added to the collapsed header.

---

## Asset detail page (kept as-is)

### AD-OPEN
- GIVEN I open an asset detail page (from holdings, movers, a watchlist row, or a signal)
- WHEN it renders
- THEN the existing asset detail view loads (header with name + Follow/Refresh, chart, quote stats,
  key issues, your-position) with no JS error and no blank screen.

> **DEFERRED (asset-detail redesign — not in this contract).** The following were specced but
> descoped at the owner's request (asset detail kept as-is); they are NOT to be QA'd here and are
> expected absent: a top "Lucius Briefing" block above the chart; 3-lens underline tabs
> (Overview/Analysis/News); key-issues rendered as bull/bear *tabs*; a graded freshness chip on the
> asset header; clickable Peers→detail. The backend that would feed these (graded `freshness`,
> cited `key_issues`) still ships and is exercised by backend tests; only the asset-detail *UI*
> redesign is deferred. Source citations ARE surfaced in the FEED (see INTEL-CITATION-CHIP).

---

## Navigation (the back-button fix)

### NAV-BACK-TO-SOURCE
- GIVEN I open an asset detail page from the Investments tab (a holdings row)
- WHEN I click "← Back" (or the browser/hardware back button)
- THEN I return to the Investments tab where I came from — NOT the Intel feed.

### NAV-BACK-FROM-OVERVIEW
- GIVEN I open an asset detail from the Overview tab "Today's movers"
- WHEN I click Back
- THEN I return to the Overview tab.

### NAV-BACK-FROM-INTEL
- GIVEN I open an asset detail from an Intel signal/watchlist row
- WHEN I click Back
- THEN I return to the Intel feed (its scroll position preserved where feasible).

### NAV-BROWSER-BACK
- GIVEN I have navigated into an asset detail page
- WHEN I press the browser back button
- THEN the asset detail closes and I land on the originating tab (the History entry pops) — not a
  full app reset.

---

## Signal lifecycle — read / dismiss / archive / delete (handoff 3)

Per `design_handoff_intel 3/CHANGES_SINCE_LAST_HANDOFF.md`. Signals are no longer permanent: each
carries server-persisted lifecycle state (read / dismissed / resolved / deleted) so it survives a
reload and the 15s poll.

### INTEL-LIFE-UNREAD
- GIVEN the feed has a signal I have not engaged with
- WHEN the feed renders
- THEN that card shows an accent unread dot at its head; once read (see below) the dot disappears
  and the card dims (~0.72 opacity).

### INTEL-LIFE-READ
- GIVEN an unread signal card
- WHEN I click its "Read" control, click "Ask Lucius", or jump to its position
- THEN the card is marked read (dot gone, card dims) and the state persists across a reload.

### INTEL-LIFE-MARK-ALL
- GIVEN the feed has N unread signals
- WHEN I click "Mark all read" (which shows the unread count N) in the feed header
- THEN every feed signal becomes read and the "Mark all read" control disappears.

### INTEL-LIFE-DISMISS
- GIVEN a signal in the feed
- WHEN I click "Dismiss"
- THEN the card leaves the feed AND the brief's "needs attention / opportunities" digest, and the
  header "Archive (N)" count increments.

### INTEL-LIFE-ARCHIVE-VIEW
- GIVEN one or more dismissed/auto-resolved signals
- WHEN I click the "Archive (N)" toggle
- THEN the feed switches to the archive view showing those signals (each with Restore/Reopen +
  Delete), and the toggle reads "Back to feed".

### INTEL-LIFE-RESTORE
- GIVEN a dismissed signal in the archive view
- WHEN I click "Restore"
- THEN it returns to the live feed and the archive count decrements.

### INTEL-LIFE-AUTORESOLVE
- GIVEN a deterministic risk signal that was breaching a threshold
- WHEN a scan runs and the condition has cleared
- THEN the signal auto-resolves: it shows a teal "Resolved" status + an "Auto-resolved · <reason>"
  banner, leaves the feed and brief for the Archive, and offers "Reopen" there. A re-breach on a
  later scan reopens it (back to action_needed).

### INTEL-LIFE-DELETE
- GIVEN a signal in the archive view
- WHEN I click the red "Delete" button and confirm "Delete forever" in the dialog
- THEN the signal is permanently removed everywhere (feed, archive, counts, brief) and does not
  return on reload.

---

## Scan-now progress indicator (handoff 3)

### INTEL-SCAN-PROGRESS
- GIVEN I confirm a scan from "Scan now"
- WHEN the scan is running
- THEN a centered bottom pill shows a spinner with "Analysing key developments ✦ / Re-scanning your
  holdings and watchlists…" and the header button shows a spinning icon + "Scanning…" (disabled).

### INTEL-SCAN-DONE
- GIVEN a scan finishes
- WHEN it completes
- THEN the pill switches to a teal-check "Up to date · N signals auto-resolved" (or "nothing new,
  your feed is current") and auto-dismisses after ~4s; it is distinct from the Ask-Lucius chat flow.

---

## Market status — batched fetch · asset-detail chip · alert-hours gating

Prices and the OPEN/CLOSED **session** come from ONE batched Yahoo v7 quote (with a per-ticker price
fallback so a batch outage never drops prices). The session is `marketState` **gated by freshness**:
Yahoo keeps reporting `PRE`/`POST` through windows where nothing actually trades — XETRA's phantom
post window and the sticky overnight `POST` (it never rolls to closed), with the price frozen at the
regular close. So a `PRE`/`POST` counts as a live extended session ONLY when the quote actually
updated within the last 30 min; otherwise the honest state is CLOSED (a XETRA ticker otherwise reads
"After hours" at 1:25 AM local). `REGULAR` is never freshness-gated. Crypto reads 24/7 via CoinGecko.
The chart `currentTradingPeriod` refines a closed symbol's "reopens in …" countdown
(holiday/DST-accurate), never the open/closed verdict. The 1D chart's session window and its
dotted pre/post bands (`session_bounds`) come from the same chart payload's `tradingPeriods` — the
session the returned bars belong to — because Yahoo rolls `currentTradingPeriod` to the NEXT
session at local midnight, hours before it opens, while the 1D bars are still yesterday's (see
ADR `2026-09-15-session-bounds-follow-the-bars-not-the-calendar-day`). See
`services/prices.market_status`.

### MKT-CHIP-OPEN
- GIVEN I open the detail page of an equity whose market is in its regular session
- WHEN the price header renders
- THEN a chip next to the price reads "Open" (green tone) with the exchange + timezone short name
  (e.g. "NasdaqGS · EDT") and no "reopens" hint.

### MKT-CHIP-CLOSED
- GIVEN I open the detail page of an equity whose market is closed
- WHEN the price header renders
- THEN the chip reads "Market closed" (muted tone) with the exchange + timezone and a
  "reopens in Xh Ym" (or "reopens <weekday> HH:MM"). The next-open is **holiday/DST-accurate** —
  the earliest FUTURE session start from Yahoo's chart `currentTradingPeriod` (cached ~20m, picks
  pre over the later regular open) — falling back to a weekend-only heuristic only when the chart
  can't yet supply a future session (right after the close, before Yahoo rolls forward).

### MKT-CHIP-OVERNIGHT (the 1:42 AM regression)
- GIVEN an equity whose post session has ended but Yahoo's `marketState` string still says `POST`
  (e.g. XETRA at 01:42 local — Yahoo sticks on POST overnight, price frozen at the regular close)
- WHEN the header renders
- THEN the chip reads "Market closed" (NOT "After hours"), because the quote hasn't updated within
  the freshness window (no actual extended-hours trading); the alert gate idles for it too.

### MKT-CHIP-EXTENDED
- GIVEN an equity in a REAL pre- or post-market session (Yahoo `PRE` / `POST` AND the quote is
  updating — `preMarketTime`/`postMarketTime`/`regularMarketTime` within the last 30 min)
- WHEN the header renders
- THEN the chip reads "Pre-market" / "After hours" (amber tone) and shows no reopen hint (it's open
  and worth polling). NOTE: a `PRE`/`POST` with a stale quote (phantom window, sticky overnight) is
  CLOSED — the chip reads "Market closed · reopens …" and the gate idles for it.

### MKT-CHIP-CRYPTO
- GIVEN I open a crypto asset (e.g. BTC)
- WHEN the header renders
- THEN the chip reads "24/7" (always-open) with no countdown.

### MKT-CHIP-UNKNOWN
- GIVEN an asset whose market state can't be resolved (non-Yahoo / v7 quote batch unavailable)
- WHEN the header renders
- THEN no market chip is shown (the rest of the header is unaffected) — never a broken/empty chip.

### MKT-GATE-CLOSED
- GIVEN I have price alerts only on markets that are currently closed
- WHEN the price-check loop fires
- THEN it makes NO live price fetch that tick (closed markets are skipped); crypto alerts keep
  polling 24/7, and a mixed set polls only the open markets.

### MKT-GATE-FAILOPEN
- GIVEN market status can't be determined for an alert ticker
- WHEN the price-check loop fires
- THEN that ticker is still polled (fail-open) — a missing calendar never silently drops an alert.

### MKT-PANEL-LABEL
- GIVEN the Monitoring panel
- WHEN I view the price-check control
- THEN it is labelled "Price check", stays visible regardless of whether I have alerts, and
  carries a helper line noting it polls only during market hours and only while alerts are set.

---

## Asset-detail price alerts (right-rail card)

A "Price Alerts" card in the asset-detail right rail (`ad-rail`, matching the Key Facts / Peers
panel style) lets you manage this asset's price thresholds without leaving the page. Alerts live on
the watchlist ticker item; the card opens the **shared `PriceAlertsModal`** — the same rich editor
used on the Intel watchlist (mute toggle, per-threshold "Instructions for Lucius" with Draft/Rewrite,
"Suggest alerts"), **minus the "Remove from watchlist" action** (`canRemove=false` on this surface).
The modal self-builds its portfolio-aware engine context (`useLuciusContext`), so the asset-detail
page does not thread a context down. Only shown for assets with a ticker. (ADR 0008.)

### ALERT-AD-EMPTY
- GIVEN an asset with a ticker and no price alerts
- WHEN the asset-detail Overview renders
- THEN the rail shows a "Price Alerts" card with "Get notified when {name} crosses a price you set."
  and a primary "+ Add price alert" button.

### ALERT-AD-LIST
- GIVEN an asset that already has price alerts
- WHEN the card renders
- THEN each alert is shown as a chip "{≥|≤|>|<} {currency}{value}" (amber, tabular) with its note
  beside it, and the button reads "Manage alerts".

### ALERT-AD-EDIT
- GIVEN I am already following the asset (a watchlist item exists)
- WHEN I click "+ Add price alert" / "Manage alerts"
- THEN the shared **PriceAlertsModal** opens titled "Price alerts · {ticker}" with the intro
  "Lucius watches {name} (now {currency}{price})…" — no extra watchlist write happens just to open
  it; adding a threshold and Save PATCHes the item (`price_alerts` + `monitor_news`) and the card
  refreshes.

### ALERT-AD-MUTE
- GIVEN the asset-detail alert modal is open and the ticker is currently un-muted (`monitor_news=true`)
- WHEN I toggle "Mute alerts" on and Save
- THEN the PATCH sets `monitor_news=false`; re-opening the modal shows the toggle still on (the
  asset-detail payload now carries `monitor_news`, so the toggle reflects reality rather than always
  defaulting to "Alerts on").

### ALERT-AD-INSTRUCTIONS
- GIVEN the asset-detail alert modal with at least one threshold row
- WHEN I use that row's "Draft"/"Rewrite" link
- THEN an "Instructions for Lucius" note is generated for that threshold (portfolio-aware when the
  asset is held) and Save persists the note on the alert — same behavior as the Intel watchlist.

### ALERT-AD-NO-REMOVE
- GIVEN the asset-detail alert modal is open
- WHEN I inspect the footer
- THEN there is **no "Remove from watchlist" button** (that action belongs to the watchlist surface;
  on asset-detail the card auto-follows on first alert and removal is via the header Follow toggle).

### ALERT-AD-AUTOFOLLOW
- GIVEN an asset I am NOT yet following
- WHEN I click "+ Add price alert"
- THEN the asset is auto-followed (one POST creates the watchlist ticker item) and the modal opens
  on the new item; after saving an alert the header reflects "★ Following".

### ALERT-AD-NOTICKER
- GIVEN an asset with no ticker symbol
- WHEN the asset-detail renders
- THEN no Price Alerts card is shown (price alerts require a ticker).

---

## Monitoring config knobs — News freshness · Comprehensive search · Digest · Advanced

The Monitoring panel surfaces the Analyst tuning knobs (Phase 3 — see ADR 0006). All controls
**save on change** (PATCH `/api/watchlist/config`, no Save button) and persist across reload.
Defaults preserve current behavior (freshness=Fast, web search On, digest On @ 17:00). Presets-first;
raw thresholds live behind a collapsed **Advanced** disclosure. Out-of-range numeric entries are
**clamped** (server-authoritative + UI). Runner note: exercise with `make qa-cu` (computer-use) or
`make qa-e2e` (browser-use) against the isolated QA server — never the live instance.

### MON-FRESHNESS-SET
- GIVEN I am on the Intel tab with Monitoring active, looking at the Monitoring panel
- WHEN I change "News freshness" from Fast to "Balanced (15m)"
- THEN it auto-saves (a "Saved" flash / no separate Save button) and the value persists after a reload.

### MON-FRESHNESS-OFF
- GIVEN the "News freshness" select
- WHEN I set it to "Off"
- THEN it saves `news_freshness=off` and persists — the fast RSS poll is no longer scheduled (the
  panel reflects "Off"); crash alerts still work (separate "Price check").

### MON-WEBSEARCH-TOGGLE
- GIVEN the "Comprehensive search (paid)" switch (default On) with its "uses search budget" hint
- WHEN I toggle it Off
- THEN it saves `web_search_enabled=false`, persists across reload, and a subsequent scan runs from
  RSS only (no paid web-search call). Toggling back On restores it.

### MON-DIGEST-TOGGLE
- GIVEN the "End-of-day digest" switch (default On)
- WHEN I toggle it Off
- THEN it saves `digest_enabled=false` and the "Delivery time" field below becomes disabled (greyed,
  aria-disabled); toggling On re-enables the time field.

### MON-DIGEST-HOUR-CLAMP
- GIVEN End-of-day digest is On
- WHEN I set "Delivery time" to 21 and blur, then set it to 30 and blur
- THEN 21 saves as `digest_eod_hour=21`; 30 snaps to 23 on blur (clamped to 0–23) and saves 23.

### MON-DIGEST-PREMARKET
- GIVEN the "Pre-market digest" switch
- WHEN I toggle it
- THEN it saves `digest_premarket_enabled` and persists across reload.

### MON-ADV-COLLAPSED
- GIVEN the Monitoring panel on first render
- WHEN I view it
- THEN the "Advanced" section is collapsed (crash %/spike fields hidden); clicking the Advanced
  header expands it (aria-expanded toggles) to reveal the threshold fields.

### MON-ADV-CRASH-CLAMP
- GIVEN the Advanced section expanded
- WHEN I set "Equity crash threshold %" to 8 and blur, then to 200 and blur
- THEN 8 saves as `crash_thresholds.equity=8`; 200 snaps to 99 on blur (clamped 1–99). (Backend
  effect: crash-watch then fires equity holdings at the configured threshold.)

### MON-ADV-SPIKE-CLAMP
- GIVEN the Advanced section expanded
- WHEN I set "Spike floor (articles)" to 100 and "Spike multiplier" to 999 and blur each
- THEN they snap to 50 and 20.0 respectively (clamped) and save — a too-low entry (0 / 0.5) snaps up
  to 1 / 1.0.

### MON-CONFIG-DISABLED-WHEN-PAUSED
- GIVEN "Monitoring active" is toggled Off
- WHEN I view the panel
- THEN the News-freshness, Comprehensive-search, digest, and Advanced controls are disabled (greyed),
  consistent with monitoring being paused.

### MON-LUCIUS-WEBSEARCH (chat parity)
- GIVEN I ask Lucius (chat) to "turn off web search for intel"
- WHEN it runs `update_monitoring_config`
- THEN `web_search_enabled` is set false (same effect as the UI toggle) and the Monitoring panel's
  "Comprehensive search" switch reflects Off after a reload.
