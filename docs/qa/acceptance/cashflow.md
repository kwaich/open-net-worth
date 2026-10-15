# Acceptance criteria — Cash Flow tab

Interaction-level GIVEN/WHEN/THEN for the **Cash Flow** tab (projection
timeline, settings modal, what-if scenario, runway hero cards).
The QA agent must exercise every criterion and report PASS/FAIL per ID.

### CF-LOAD
- GIVEN at least one account exists
- WHEN I click "Cash Flow" in the sidebar
- THEN the tab loads with the "Cash position" and "Financial runway" hero
  cards, the outlook chips (Lowest point, Net over N months, Tight months),
  and the month list — no JS error, no blank screen.

### CF-MONTH-ROWS
- GIVEN the Cash Flow tab is loaded
- WHEN I view the month list
- THEN each row shows a month label, In, Out, Net, and Balance columns;
  past months bear an "Actual" badge, and the current month bears a
  "Today" badge.

### CF-EXPAND-MONTH
- GIVEN a month row has one or more non-recurring events (shown via the
  "N events" count chip)
- WHEN I click that month row
- THEN the events expand below the row, each showing a category badge,
  a signed amount, and an event label.

### CF-FILTER-EVENTS
- GIVEN the Cash Flow tab is loaded with at least one month having events
- WHEN I click "Has events" in the month-filter SegmentedControl
- THEN only months with at least one non-recurring event are shown in the list.

### CF-FILTER-TIGHT
- GIVEN at least one projected month has a balance below the low-balance
  threshold (shown as "Tight months" > 0 in the outlook chips)
- WHEN I click "Tight only" in the SegmentedControl
- THEN only tight/negative-balance months are shown in the list.

### CF-SETTINGS-OPEN
- GIVEN I am on the Cash Flow tab
- WHEN I click "Settings"
- THEN the "Projection settings" modal opens with fields for Monthly income,
  Monthly expenses, Expense growth %, and Income growth % — and a "Reset
  all to defaults" button in the footer.

### CF-WHAT-IF-INCOME
- GIVEN the Settings modal is open
- WHEN I change the Monthly income field to a different value (e.g. double
  the current amount) and close the modal
- THEN the projected month rows re-compute: Net figures change, the runway
  figure in the hero card updates, and the "Settings (active)" button label
  indicates an active what-if.

### CF-RESET-SETTINGS
- GIVEN a what-if scenario is active (Settings button reads "Settings (active)")
- WHEN I open Settings and click "Reset all to defaults"
- THEN the modal resets all fields to their computed actuals, the projection
  re-loads with the default baseline, and the button label reverts to plain
  "Settings".

### CF-ASSET-SCOPE
- GIVEN the Settings modal is open and there are investment positions
  (equity, crypto, etc.)
- WHEN I check an asset-class checkbox in the "Runway asset scope" section
  (e.g. "Equity")
- THEN the Cash position hero card updates to include that class's market
  value in the liquid balance and the runway figure lengthens accordingly.

### CF-COMMITMENTS-TOGGLE
- GIVEN at least one active commitment has upcoming installment payments
- WHEN I open Settings, uncheck "Include commitment payments & funding",
  and close the modal
- THEN the projected month rows change (commitment cashflow events disappear
  or totals change), and the "Settings (active)" button confirms the change.

### CF-SUMMARY-STRIP
- GIVEN the Cash Flow tab has at least one projected future month
- WHEN I view the CashSummary strip below the header
- THEN four stats are shown: Avg savings rate (a percentage), Avg net / month
  (a signed currency figure), Runway, and Projected end balance — none are
  blank or show "—" when data is available.
