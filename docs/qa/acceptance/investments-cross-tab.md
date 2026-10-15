# Acceptance criteria — Investments / Cross-tab features (Wave 2)

Interaction-level GIVEN/WHEN/THEN for the cross-tab features of the Investments redesign:
the priority-ranked Portfolio Brief at the top of the Investments tab (Concentration →
Rebalance → Vesting → Income → Today → On-track), the "N to decide" pill, the real 30-day
masthead sparkline (omitted when fewer than 2 priced days), per-sub-tab empty states, and
the Activity → Tax deep-link.

The QA agent must exercise every criterion and report PASS/FAIL per ID. Run against a QA
instance on ports 8090–8099 with `ONW_QA_MODE=1`.

---

## Portfolio Brief — priority-ranked

### INV-C-BRIEF-VISIBLE
- GIVEN the Investments tab is active and positions exist
- WHEN the tab loads
- THEN a "Portfolio Brief" section is visible near the top of the Investments tab
  (above or alongside the sub-tab control) showing a compact, ranked list of
  portfolio attention items.

### INV-C-BRIEF-CONCENTRATION-FIRST
- GIVEN the portfolio has a concentration issue (a single asset or region exceeds a
  configured guideline, e.g. >40% in one stock)
- WHEN I view the Portfolio Brief
- THEN "Concentration" is the first (highest-priority) item listed, with a brief
  description of the issue (e.g. "AAPL is 45% of your portfolio").

### INV-C-BRIEF-REBALANCE
- GIVEN allocation targets are set and drift exceeds the rebalance threshold
- WHEN I view the Portfolio Brief
- THEN a "Rebalance" item appears in the brief, ranked after Concentration (if present)
  and before Vesting, describing which position is most off-target.

### INV-C-BRIEF-VESTING
- GIVEN active equity grants with vests scheduled within the next 30 days
- WHEN I view the Portfolio Brief
- THEN a "Vesting" item appears, ranked after Rebalance, listing the upcoming vest(s)
  and their indicative value.

### INV-C-BRIEF-INCOME
- GIVEN dividend income is expected within 30 days (from the dividend calendar or income
  forecast)
- WHEN I view the Portfolio Brief
- THEN an "Income" item appears, ranked after Vesting, summarizing expected near-term
  dividend income in base currency.

### INV-C-BRIEF-TODAY
- GIVEN the market session has produced notable movers today (e.g. a holding moved >2%)
- WHEN I view the Portfolio Brief
- THEN a "Today" item appears, ranked after Income, calling out the most significant
  day move (e.g. "NVDA +4.2% today").

### INV-C-BRIEF-ON-TRACK
- GIVEN no concentration, rebalance, vesting, income, or significant today items exist
  (or all are within acceptable thresholds)
- WHEN I view the Portfolio Brief
- THEN an "On-track" item is shown as the fallback (e.g. "Portfolio is on track —
  no actions needed") — the brief is NEVER blank.

### INV-C-BRIEF-ORDERED
- GIVEN multiple brief items exist simultaneously (e.g. both a concentration issue and
  an upcoming vest)
- WHEN I view the Portfolio Brief
- THEN items appear in the defined priority order: Concentration first, then Rebalance,
  Vesting, Income, Today, On-track last — never in an arbitrary or randomized order.

---

## "N to decide" pill

### INV-C-PILL-SHOWS
- GIVEN at least one brief item requires a decision (e.g. a rebalance action is suggested
  or a vest is pending confirmation)
- WHEN I view the Portfolio Brief
- THEN an "N to decide" pill is visible adjacent to the brief header, where N is the
  count of actionable items.

### INV-C-PILL-COUNT-ACCURATE
- GIVEN there are exactly 2 actionable items (e.g. 1 rebalance + 1 pending vest)
- WHEN I view the "N to decide" pill
- THEN the pill reads "2 to decide" — the count matches the number of decision-requiring
  brief items exactly.

### INV-C-PILL-HIDDEN-ZERO
- GIVEN the Portfolio Brief shows only the "On-track" fallback item (no decisions needed)
- WHEN I view the brief header
- THEN the "N to decide" pill is NOT shown — it is hidden when N = 0.

### INV-C-PILL-UPDATES
- GIVEN the "2 to decide" pill is shown
- WHEN I take action on one of the items (e.g. confirm a vest, or mark a rebalance as done)
- THEN the pill updates to "1 to decide" (or disappears if N reaches 0) — it is NOT
  frozen on the old count.

---

## Real 30-day masthead sparkline

### INV-C-SPARKLINE-VISIBLE
- GIVEN the Investments tab is active and at least 2 days of priced portfolio history
  exist within the last 30 days
- WHEN I view the masthead / top of the Investments tab
- THEN a 30-day sparkline chart is visible, rendered with real data points for each
  priced day.

### INV-C-SPARKLINE-OMITTED-LESS-THAN-2
- GIVEN fewer than 2 priced days of portfolio history exist in the last 30 days
  (e.g. a brand-new account with only today's prices)
- WHEN I view the masthead sparkline area
- THEN the sparkline is NOT shown — the space is empty or shows a placeholder "Insufficient
  history for chart"; a flat synthetic line (e.g. a horizontal line drawn from a single
  data point) is never rendered.

### INV-C-SPARKLINE-NO-SYNTHETIC
- GIVEN exactly 1 priced day exists in the last 30 days
- WHEN I inspect the sparkline area
- THEN there is no line chart rendered at all — not a zero-slope line, not a dot, and
  not any visual that could be misread as a meaningful price trend.

### INV-C-SPARKLINE-DATA-POINTS
- GIVEN 10 priced days exist in the last 30 days
- WHEN the sparkline renders
- THEN it has exactly 10 data points (one per priced day), not interpolated points for
  unpriced days — the sparkline reflects real pricing events only.

### INV-C-SPARKLINE-COLOR
- GIVEN the sparkline is visible
- WHEN the portfolio value at the end of the 30-day window is higher than at the start
- THEN the sparkline is rendered in a positive color (e.g. green); when lower, in a
  negative color (e.g. red); when flat (same start and end), in a neutral color.

---

## Per-sub-tab empty states

### INV-C-EMPTY-HOLDINGS-NEW
- GIVEN no investment positions exist (fresh install)
- WHEN I navigate to Investments → Holdings
- THEN an empty state is shown specific to Holdings: e.g. "No holdings yet" with an
  action to add a position — NOT a generic "no data" message.

### INV-C-EMPTY-PERFORMANCE-NEW
- GIVEN no investment positions exist
- WHEN I navigate to Investments → Performance
- THEN an empty state specific to Performance is shown (e.g. "Add positions to start
  tracking performance") — NOT the Holdings empty state, and NOT a blank screen.

### INV-C-EMPTY-ACTIVITY-NEW
- GIVEN no investment-related transactions exist
- WHEN I navigate to Investments → Activity
- THEN an empty state specific to Activity is shown (e.g. "No investment activity yet") —
  NOT the Holdings or Performance empty state.

### INV-C-EMPTY-OPTIONS-NEW
- GIVEN no option positions exist
- WHEN I navigate to Investments → Options
- THEN an empty state specific to Options is shown — NOT the generic Holdings empty state.

### INV-C-EMPTY-EACH-DISTINCT
- GIVEN a fresh install with no data
- WHEN I visit each Investments sub-tab in turn (Holdings, Performance, Activity, Options)
- THEN each shows a different empty-state message appropriate to that sub-tab's content —
  no two sub-tabs share an identical empty-state copy.

---

## Activity → Tax deep-link

### INV-C-DEEPLINK-TAX-FROM-ACTIVITY
- GIVEN I am on the Investments → Activity sub-tab and a realized G/L callout is visible
- WHEN I click the "View full breakdown" link in the callout
- THEN the application navigates to the Tax tab (not a modal, not an in-page panel) and
  the Tax tab's realized gains section is visible on arrival.

### INV-C-DEEPLINK-TAX-ACTIVE
- GIVEN I clicked the Activity → Tax deep-link and arrived at the Tax tab
- WHEN I look at the active tab in the sidebar
- THEN "Tax" is the selected/active item in the navigation — the sidebar correctly reflects
  the current location, and the Investments tab is NOT still shown as active.

### INV-C-DEEPLINK-TAX-BACK
- GIVEN I arrived at the Tax tab via the Activity deep-link
- WHEN I click the browser Back button (or the app's back affordance, if present)
- THEN I return to the Investments Activity sub-tab, not to an unrelated page.

### INV-C-DEEPLINK-TAX-NO-RECOMPUTE
- GIVEN I arrived at the Tax tab via the deep-link
- WHEN I view the realized gains figures
- THEN the figures match what the Activity sub-tab callout showed — the Tax tab does NOT
  recompute different figures (both surfaces read from the same realized-events source).
