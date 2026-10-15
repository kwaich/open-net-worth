# Acceptance criteria — Investments / Options sub-tab (Wave 2)

Interaction-level GIVEN/WHEN/THEN for the new **Options** sub-tab: two strategy sections
(long options, covered calls), per-row layout, ≤ 14-day amber expiry chip, summary stats,
covered-call→underlying linkage, wide-table horizontal scroll, and empty state.

The QA agent must exercise every criterion and report PASS/FAIL per ID. Run against a QA
instance on ports 8090–8099 with `ONW_QA_MODE=1`. Seed at least: one long call/put position,
one covered-call position linked to an underlying equity, one option expiring within 14 days,
and one expired option.

---

## Two strategy sections

### INV-O-SECTIONS-VISIBLE
- GIVEN at least one option position exists
- WHEN I navigate to the Options sub-tab
- THEN two distinct sections are visible: "Long options" and "Covered calls" (or equivalent
  section headers) — the sub-tab does NOT show a single flat list that mixes strategies.

### INV-O-SECTION-LONG
- GIVEN a long call or put option position is recorded
- WHEN I view the "Long options" section
- THEN the long option appears in that section with its own row; it does NOT appear in
  the "Covered calls" section.

### INV-O-SECTION-COVERED-CALL
- GIVEN a covered-call position is recorded
- WHEN I view the "Covered calls" section
- THEN the covered call appears in that section and NOT in the "Long options" section.

### INV-O-SECTION-EMPTY-LONG
- GIVEN covered-call positions exist but no long options
- WHEN I view the Options sub-tab
- THEN the "Long options" section shows its empty state ("No long options") and the
  "Covered calls" section is populated — both sections are always rendered (never hidden
  entirely when the other section has data).

### INV-O-SECTION-EMPTY-COVERED
- GIVEN long option positions exist but no covered calls
- WHEN I view the Options sub-tab
- THEN the "Covered calls" section shows its empty state; the "Long options" section
  is populated.

---

## Per-row layout

### INV-O-ROW-COLUMNS
- GIVEN options are listed in their respective sections
- WHEN I read a row
- THEN the row shows at minimum: Ticker / contract description, Strike, Expiry date,
  # of contracts, Current price (mark), P&L (unrealized), and Expiry chip (when applicable).
  All monetary / numeric columns are right-aligned with tabular numerics.

### INV-O-ROW-LONG-PNL
- GIVEN a long call option with a known cost basis and current mark
- WHEN I view its row
- THEN the P&L column shows unrealized P&L = (current mark − cost basis) × contracts × multiplier,
  with correct sign (positive if the option gained value, negative if it lost).

### INV-O-ROW-COVERED-CALL-PREMIUM
- GIVEN a covered call with a recorded premium received
- WHEN I view its row in the covered calls section
- THEN the premium received is shown (positive, since it was income); the current mark
  (the liability) is shown separately so the user can see net P&L for the position.

---

## ≤ 14-day amber expiry chip

### INV-O-EXPIRY-CHIP-14D
- GIVEN an option expiring within 14 calendar days of today
- WHEN I view its row
- THEN an amber/orange "Expires in N days" chip is visible on that row, where N is the
  correct number of days to expiry (not stale from a prior date).

### INV-O-EXPIRY-CHIP-TOMORROW
- GIVEN an option expiring tomorrow
- WHEN I view its row
- THEN the amber chip reads "Expires tomorrow" (or "Expires in 1 day") — the phrasing
  is human-readable, not a raw ISO date.

### INV-O-EXPIRY-CHIP-TODAY
- GIVEN an option expiring today
- WHEN I view its row
- THEN the expiry chip reads "Expires today" and is styled in a more urgent tone (e.g.
  red or strong amber) compared to a 14-day chip.

### INV-O-EXPIRY-CHIP-ABSENT-FAR
- GIVEN an option expiring more than 14 days from today
- WHEN I view its row
- THEN NO amber expiry chip is shown; the expiry date is shown in the Expiry column
  as a plain date only.

### INV-O-EXPIRY-CHIP-EXPIRED
- GIVEN an option past its expiry date
- WHEN I view the Options sub-tab
- THEN the expired option row shows an "Expired" state chip (or is moved to an "Expired"
  sub-section); it is NOT shown with the same styling as an active position, and no
  amber "Expires in N days" chip references a past date.

---

## Summary stats

### INV-O-SUMMARY-VISIBLE
- GIVEN at least one option position exists
- WHEN I view the Options sub-tab
- THEN a summary stats bar or card is visible, showing aggregate figures across all option
  positions.

### INV-O-SUMMARY-TOTAL-VALUE
- GIVEN the summary stats are visible
- WHEN I read the total value stat
- THEN it shows the sum of current mark values across all active option positions in
  base currency; the figure is consistent with summing the per-row values.

### INV-O-SUMMARY-TOTAL-PNL
- GIVEN the summary stats are visible
- WHEN I read the total P&L stat
- THEN it shows the sum of unrealized P&L across all active option positions, with
  correct sign and consistent with summing the per-row P&L column.

### INV-O-SUMMARY-EXPIRING-SOON
- GIVEN at least one option is expiring within 14 days
- WHEN I view the summary stats
- THEN a "Expiring soon" count is shown (e.g. "2 expiring ≤14d") so the user can
  see at a glance how many positions need attention.

---

## Covered call → underlying linkage

### INV-O-CC-UNDERLYING-LINK
- GIVEN a covered call position is linked to an underlying equity holding
- WHEN I view the covered call's row in the Covered calls section
- THEN the underlying ticker is shown and is clickable (a link or reference); clicking
  it navigates to the underlying equity's holdings row or asset detail — it does NOT
  open an unrelated screen.

### INV-O-CC-UNDERLYING-COVERAGE
- GIVEN a covered call is linked to an underlying equity
- WHEN I view the row
- THEN the "Coverage" indicator shows how many shares are covered (e.g. "100 shares
  covered" for 1 contract on a 100-multiplier equity) relative to the underlying position
  size, so the user can see if they are fully vs partially covered.

### INV-O-CC-UNLINKED-WARNING
- GIVEN a covered call exists but no underlying equity holding is found in the portfolio
- WHEN I view its row
- THEN a warning indicator is shown (e.g. "No underlying position found") rather than
  silently showing blank coverage data; the user is informed the linkage is broken.

---

## Wide-table horizontal scroll

### INV-O-WIDE-SCROLL-MOBILE
- GIVEN the viewport is narrower than the options table's natural width
- WHEN I view the Options sub-tab
- THEN the table container is horizontally scrollable — the table does NOT reflow to
  a broken layout and does NOT clip columns outside the viewport edge.

### INV-O-WIDE-SCROLL-COLUMNS-INTACT
- GIVEN I scroll horizontally on a narrow viewport
- WHEN I reach the rightmost columns
- THEN all columns (including Strike, Expiry, P&L, and the expiry chip) are visible and
  readable; no column is missing from the scrollable area.

### INV-O-WIDE-SCROLL-HEADER-STICKY
- GIVEN I scroll the options table horizontally
- WHEN the column headers are present
- THEN the column headers scroll WITH the table body (not a separate sticky-header that
  misaligns with the data columns during scroll).

---

## Empty / loading / error states

### INV-O-EMPTY-NO-OPTIONS
- GIVEN no option positions exist
- WHEN I navigate to the Options sub-tab
- THEN an EmptyState is shown (e.g. "No option positions — record a covered call or long
  option to see them here") — not a blank screen, not two empty section headers with no
  explanation, and no JS error.

### INV-O-LOADING
- GIVEN the Options sub-tab is loading position data
- WHEN the request is in-flight
- THEN a loading skeleton or spinner is visible — the sub-tab does not flash an empty
  state before data arrives.

### INV-O-ERROR
- GIVEN the backend returns a 5xx error for the portfolio endpoint
- WHEN the Options sub-tab attempts to load
- THEN an error state is shown with a Retry button; no partial data is shown alongside
  the error, and no raw error text or stack trace is exposed.
