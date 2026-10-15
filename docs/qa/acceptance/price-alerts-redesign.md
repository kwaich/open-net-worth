# Acceptance — Price alerts: manage + create (the redesign)

The per-ticker Price Alerts modal is two views: **Manage** (every alert on the ticker, the
52-week track, Lucius's suggestions, mute and news sources — every control commits itself) and
a **Form** (one alert: name, criteria, cooldown, notifications, instructions). Reached from the
Intel watchlist rail (a ticker row's edit chevron), All monitors → Price alerts, and the
asset-detail Price Alerts card ("Manage" / "+ Add price alert").

Context: `frontend/src/components/intel/PriceAlertsModal.tsx` (host),
`frontend/src/components/intel/priceAlerts/` (views, model, stylesheet), `/api/alerts`,
`/api/watchlist/items/{id}`, `/api/intel/assets/{id}/stats`. Decision:
`docs/adr/2026-09-21-price-alerts-redesign-faithful-port.md`.

### PAR-MANAGE-LIST
- GIVEN a ticker with two alerts, one named "Good entry zone" at ≤ 182 and one unnamed at ≥ 285, and a live price of 190.24
- WHEN I open its Price Alerts modal
- THEN the title reads `<TICKER> · <Name>`, the header shows the live price and day change, and the Alerts panel lists both rows — "Good entry zone" and "Rises above $285.00" — each with its cooldown, channel and distance ("4.3% away", "49.8% away"), the panel badge reading "2 of 2 on"

### PAR-MANAGE-TRACK
- GIVEN the same ticker and a quote source that reports a 52-week range
- WHEN the modal opens
- THEN a horizontal track shows a "now" marker at the live price and one tick per price level, with the "52w range" line beneath; a level within 5% of firing is amber

### PAR-MANAGE-EMPTY
- GIVEN a ticker with no alerts
- WHEN I open its Price Alerts modal
- THEN I see "No alerts on <TICKER> yet" with a **New alert** action, and no suggestion box or alert panel

### PAR-ROW-TOGGLE
- GIVEN the manage view lists an enabled alert
- WHEN I flip its row switch off
- THEN the row dims, the badge count drops by one, and exactly one `PATCH /api/alerts/{id}` with `{"enabled": false}` is sent — no Save button exists

### PAR-MUTE
- GIVEN the ticker is on a watchlist
- WHEN I flip **Mute <TICKER>** on
- THEN "Muted" appears in the header, a "Saved" tick flashes beside the switch, and one `PATCH /api/watchlist/items/{id}` carries `{"monitor_news": false}`

### PAR-NO-ITEM
- GIVEN a ticker with alerts but on no watchlist (a standalone alert in All monitors)
- WHEN I open its modal
- THEN the alert list works as usual and the Mute / News sources settings are not rendered

### PAR-FEEDS
- GIVEN the manage view with **News sources** expanded
- WHEN I click **Add feed**, type a feed URL, and leave the field
- THEN one `PATCH /api/watchlist/items/{id}` carries `custom_feed_urls` with that URL, "Saved" flashes, the counter reads "4 of 5 left"; an empty row is dropped on blur; a URL the server rejects shows the server's reason inline
- AND the section's help text states the exact cadence ("every 90 seconds", the hot RSS lane's fixed tick) and, when RSS freshness is off in Monitoring settings, says the feeds are paused

### PAR-SUGGEST
- GIVEN the manage view of a ticker with at least one alert
- WHEN I click **Suggest**
- THEN a "Lucius suggests N alerts" box lists each level with its reasoning; **Add** on one creates it immediately (`POST /api/alerts` with the suggestion's name, a 24-hour cooldown and push notifications) and removes it from the box; **Skip** removes it without a write; **Add all** creates every remaining one

### PAR-NEW-FORM
- GIVEN the manage view
- WHEN I click **New alert**
- THEN the form opens pre-filled from Lucius's first suggestion (direction, level, name, note), the Ticker select is locked to this ticker, and the footer offers **Cancel** and **Create alert**

### PAR-CREATE
- GIVEN the New alert form
- WHEN I set the name "Stop-loss zone", pick Falls below with a target under the live price, choose Cooldown "Turn off" and Notifications "In-app, push and email", and click **Create alert**
- THEN exactly one `POST /api/alerts` is sent with `name`, `kind: "price"`, `op: "<="`, `on_trigger: "disable"`, `notify: "push_email"`, and the manage view returns showing the new row

### PAR-ALREADY-PAST
- GIVEN the New alert form with Rises above selected
- WHEN I type a target below the live price
- THEN the field is flagged invalid with "<TICKER> is already above this level — it would alert right away"

### PAR-MOVE
- GIVEN the New alert form
- WHEN I switch Alert criteria to **% move**, pick Falls by, enter 7.5 and click **Create alert**
- THEN the default query reads "a 7.50% fall in <TICKER>", and the `POST` carries `kind: "move"`, `op: "<="`, `value: 7.5`; the new row reads "Falls 7.50% in a day" with a ↓ 7.50% chip and today's move in the distance column

### PAR-EDIT
- GIVEN the manage view
- WHEN I click an alert row, flip the Active switch to Paused, change the target, and click **Save alert**
- THEN exactly one `PATCH /api/alerts/{id}` carries the new value and `enabled: false`; no `POST` or `DELETE` is sent; a `>`/`<` alert saved unedited keeps its strict op

### PAR-DELETE
- GIVEN the edit form of an existing alert
- WHEN I click **Delete alert**
- THEN one `DELETE /api/alerts/{id}` is sent and the manage view returns without that row

### PAR-SAVE-FAIL
- GIVEN the edit form and a server that answers the write with an error
- WHEN I click **Save alert**
- THEN the form stays open with "Could not save — please try again." in the footer

### PAR-BACK
- GIVEN the manage view opened from the Intel watchlist rail
- WHEN I click **Back to watchlist** (or **Close** from the asset-detail card)
- THEN the modal closes; the rail/card reflects any writes made (a GET-only refetch)

### PAR-FIRE-MOVE
- GIVEN a `kind: "move"`, `>= 5` alert on a ticker whose previous close is 100
- WHEN the price monitor sees 106 (and not at 104)
- THEN the alert fires once with a narration of the day move ("has risen 6.0% today, past your 5% move alert"), keyed apart from a `>= 5` price alert

### PAR-NOTIFY
- GIVEN alerts with `notify: "in_app"`, `"push"` and `"push_email"` and configured `alert_channels: ["telegram"]`
- WHEN each fires
- THEN the in-app story is written for all three; no external push for `in_app`; Telegram for `push`; Telegram plus email for `push_email` only when the email channel is available
