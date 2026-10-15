# Acceptance criteria — Overview tab

Interaction-level GIVEN/WHEN/THEN for the **Overview** tab (net-worth hero,
allocation charts, FIRE projection, AI briefing, net-worth history chart).
The QA agent must exercise every criterion and report PASS/FAIL per ID.

### OV-LOAD
- GIVEN I am on any tab
- WHEN I click "Overview" in the sidebar
- THEN the Overview tab loads with no JS error: the net-worth SummaryBar (four
  cards: Net worth, Unrealized P&L, Today, Cash on hand) is visible and the
  Allocation and FIRE sections are rendered.

### OV-EMPTY-DEMO
- GIVEN there are no accounts or holdings (fresh install)
- WHEN I view the Overview tab
- THEN an empty-state screen is shown with three action buttons: "Add first
  account", "Import a statement", and "Load demo data" — not a blank screen
  or a zero-figure dashboard.

### OV-DEMO-SEED
- GIVEN I am on the empty Overview screen
- WHEN I click "Load demo data"
- THEN a loading indicator appears on the button, then the dashboard re-loads
  showing non-zero net worth, allocation charts, and FIRE stats.

### OV-HERO-STATS
- GIVEN there is at least one account with a balance
- WHEN I view the Overview tab
- THEN the SummaryBar shows a numeric net-worth value (not "—"), and the
  "Cash on hand" card shows a non-zero number (never blank or undefined).

### OV-ALLOCATION-TOGGLE
- GIVEN the Overview tab is loaded with holdings data
- WHEN I click the "Allocation" collapsible-section header
- THEN the donut chart and accessibility bar chart hide (section collapses),
  and clicking the header again re-shows them — charts re-render with
  non-blank canvases (no empty grey boxes).

### OV-ALLOC-CHART-DATA
- GIVEN the Allocation section is open with investment positions
- WHEN I hover over the donut chart
- THEN a tooltip appears showing the asset class name, its base-currency
  value, and its percentage of total.

### OV-WHERE-MONEY-HELD
- GIVEN I am on the Overview tab with at least one account
- WHEN I view the "Where money is held" collapsible section (default open)
- THEN at least one row showing an account/platform and its base-currency
  balance is visible.

### OV-NW-HISTORY-CHART
- GIVEN at least two net-worth history snapshots exist (i.e. the app has
  been run on at least two dates)
- WHEN I view the Overview tab
- THEN the "Net worth over time" panel is shown with a bar+line chart and
  its caption note below it.

### OV-FIRE-SECTION
- GIVEN FIRE profile fields (date of birth, target retirement age, monthly
  savings) are configured
- WHEN I view the Overview tab
- THEN the FIRE Projection section shows a FIRE number, a progress %, a
  "Years to FI" figure, and a trajectory line chart with two datasets
  (Net Worth and FIRE Target).

### OV-NEEDS-ATTENTION
- GIVEN there is outstanding debt or a position concentration ≥ 30% of NW
- WHEN I view the "Needs attention" panel
- THEN at least one risk flag is shown (not "No flags from current data").

### OV-COMMITMENT-STRIP
- GIVEN at least one active commitment has upcoming events within 120 days
- WHEN I view the Overview tab
- THEN the "Upcoming commitments" collapsible section is shown with at least
  one row listing a date, kind badge, label, and days-until figure.

### OV-BRIEFING-EXPAND
- GIVEN an AI model is configured
- WHEN I view the Overview tab and the WealthBriefing card is present
- THEN clicking the card header (or its expand control) reveals the briefing
  body content (it was collapsed by default) — the card does not stay blank.

### OV-PRIVACY-MODE
- GIVEN the Overview tab is loaded with data and charts are rendered
- WHEN I press "H" or toggle "Hide amounts" (privacy mode ON)
- THEN every Overview chart canvas y-axis tick displays `***` and the net-worth chart tooltip values display `***` without reloading; and when privacy mode is toggled OFF, real compact currency ticks and amounts return immediately.
