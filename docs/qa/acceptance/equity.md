# Acceptance criteria — Equity Compensation

Interaction-level GIVEN/WHEN/THEN for the equity-compensation feature (RSU/ESOW grants,
vest events, tax-at-vest). The QA agent must exercise every criterion and report PASS/FAIL
per ID. (`EQ-VEST-POST` and `EQ-TAX-ONCE` are the ones that would catch silent
double-posting or double-counting regressions.)

## Environment notes

- Run against a QA instance on ports **8090–8099** with `ONW_QA_MODE=1` set.
- **Never** drive the dev server on `:5174` or `:8080` for QA.
- All examples use synthetic data. Instrument **ONWAI** (an existing priced instrument)
  is used throughout for realism.
- Where criteria reference the vest choke point, verify via the API response and
  `GET /api/transactions` / `GET /api/lots` — do NOT rely on the UI alone.

---

### EQ-GRANT-CREATE
- GIVEN I create a grant: ONWAI, granted 2024-03-15, 1,200 units, tranches
  [300 @ 2025-03-15, 300 @ 2026-03-15, 300 @ 2027-03-15, 300 @ 2028-03-15], tax_event=at_vest
- THEN the grant shows 1,200 unvested / 0 vested, next vest 2025-03-15, and the unvested
  units appear in net worth at market value under the `locked` bucket.

### EQ-VEST-POST
- GIVEN the 2025-03-15 tranche of that grant
- WHEN I mark it vested with FMV 30.00 (currency as entered) and 300 units
- THEN exactly one income transaction (kind=income, category `equity-compensation`,
  taxable, amount 9,000.00) and one lot (300 units, cost basis 30.00/unit,
  acquired_at 2025-03-15) are created via the existing choke points, the grant shows
  300 vested / 900 unvested, and 300 units move locked → liquid.

### EQ-8B-REPORT
- GIVEN several vest events across multiple grants in one tax year
- WHEN I open the equity tax-year report
- THEN each row shows grant date, vest date, price paid, FMV at vest, units, and gross
  gain = units × (FMV − price paid); the total equals the sum exactly (±0.01); and the
  estimated-tax card shows total × configured income rate (or "—" when unconfigured).

### EQ-TAX-DEFERRED
- GIVEN a grant with `tax_event=deferred`
- WHEN a tranche vests
- THEN the lot and bucket flip still happen, but NO taxable income transaction is
  posted (the vest is excluded from the tax-year gains total) — the position's basis
  carries the facts needed for tax-at-sale interpretation later.

### EQ-VEST-CALENDAR
- GIVEN active grants with future tranches
- WHEN I view the equity section (or ask Lucius)
- THEN I see upcoming vests ordered by date with units and indicative value at current
  price, and a notification fires on the vest date prompting me to confirm the vest.

### EQ-VEST-HISTORY
- GIVEN I have recorded one or more vest events
- WHEN I view Investments → Equity Compensation
- THEN a "Vest history" section appears directly below "Upcoming vests"
- AND its rows are ordered newest vest first
- AND every row shows the vest date, symbol, unit count, and "Released", "Awaiting
  release", or "Not released" from persisted delivery facts
- AND a vest-date lot alone never invents a release date for a legacy event.

### EQ-VEST-HISTORY-RELEASE
- GIVEN a vest-history row is awaiting release
- WHEN I click "Mark released" and confirm a valid release date
- THEN the same row remains in Vest history and changes to "Released"
- AND it shows the actual release date instead of disappearing from the screen.
- AND a legacy row with an existing lot records the date only, without adding a second
  lot, changing position quantity, or writing another income transaction.

### EQ-VEST-HISTORY-EMPTY
- GIVEN the grants have no recorded vest events
- WHEN I view Investments → Equity Compensation
- THEN the Vest history section says "No vest history yet" and does not invent a
  released or pending row from the grant totals.

### EQ-VEST-HISTORY-RESPONSIVE
- GIVEN nine persisted vest events on a phone-width viewport
- WHEN I expand Vest history and use Mark released
- THEN dates, holdings, statuses, and actions remain readable and tappable without
  horizontal page scrolling.
- AND on desktop the four visible columns use the full panel width, with the release
  action inside the Status column rather than an unused fifth column.

### EQ-FMV-PREFILL
- GIVEN ONWAI has a price in price_history at/near the vest date
- WHEN I start marking a vest
- THEN FMV is pre-filled from that price (clearly editable — broker statement wins).

### EQ-UNDO
- GIVEN a vest event posted by mistake
- WHEN I delete/undo it
- THEN the income transaction and lot are reversed consistently (no orphan lot, no
  orphan txn, units return to unvested) — same-turn checkpoint restore also works.

### EQ-NW-SPLIT
- GIVEN vested and unvested ONWAI units
- WHEN I view Overview / risk panel
- THEN the by-accessibility split and employer-concentration figures include unvested
  units at market value (concentration must NOT understate employer exposure).

---
## Implied criteria (not enumerated in the PRD but required by D4/D5/P4-TX-INC)

### EQ-VEST-CALENDAR-NOTIFY
- GIVEN an active grant with a tranche whose vest_date is today
- WHEN the daily `vest_reminder` scheduled task runs
- THEN a notification fires prompting the user to confirm the vest for that tranche,
  AND no notification fires for tranches whose vest_date is in the future.

### EQ-TAX-ONCE
- GIVEN a vest event whose income transaction was posted at_vest (taxable=true,
  category=equity-compensation)
- WHEN I view the Tax tab employment income section
- THEN the vest income is counted exactly once in the "Employment / other income"
  taxable_base — it does NOT also appear in the equity report gross-gains total as a
  separate line, and it is NOT classified as investment income (no double-count between
  the tax breakdown and the equity report).

---
## Equity polish — lane/equity (AddGrantModal, MarkVestModal, EquityTab grant drawer)

### EQ-PASTE-FILL
- GIVEN the Add-grant modal is open
- WHEN I paste "1,000 NVDA RSUs, quarterly over 4 years from 2026-09-15" into the
  Lucius paste-box and click "Fill in with Lucius ✦"
- THEN the ticker field shows NVDA, total units 1000, frequency quarterly, years 4,
  first-vest 2026-09-15 and a note "Lucius filled in 5 fields — review below" appears.

### EQ-PASTE-FAIL
- GIVEN the Add-grant modal is open
- WHEN I paste "hello world" into the paste-box and click "Fill in with Lucius ✦"
- THEN a "Couldn't parse any fields" note appears and NO form fields are modified.

### EQ-SCHED-GENERATE
- GIVEN the Add-grant modal in Schedule mode with total=1000, frequency=quarterly,
  years=4, first-vest=2026-09-15
- WHEN I click "Preview"
- THEN 16 tranches are shown, the first is 62 units on 2026-09-15, the last is 70
  units (remainder), all dates are monotonically increasing, and the total sums to 1000.
  Save is enabled once a valid account is selected.

### EQ-SCHED-EDIT-MANUALLY
- GIVEN schedule mode with a generated schedule (as above)
- WHEN I click "Edit manually"
- THEN the mode switches to manual, the tranches table is pre-populated with the 16
  generated rows (all editable), and I can add or remove rows.

### EQ-ADVANCED-DISCLOSURE
- GIVEN the Add-grant modal
- WHEN I click the "Advanced ▸" toggle
- THEN the grant date, price at grant, grant type, tax event, and account fields expand;
  clicking again collapses them.

### EQ-PRICE-PAID
- GIVEN MarkVestModal open for a vest of 100 units at FMV 50.00
- WHEN I set price-paid = 10.00 in the "Price paid per unit" field
- THEN the preview shows gross gain 4,000 (100 × (50 − 10)) and the formula
  "100 × (50.00 − 10.00)" is visible; the POST body includes `price_paid_per_unit: 10`.

### EQ-PRICE-PAID-DEFAULT
- GIVEN MarkVestModal for a standard RSU grant
- WHEN the modal opens
- THEN price-paid defaults to 0 and the gross-gain formula shows "N × (FMV − 0.00)".

### EQ-UNDO-DIALOG
- GIVEN the expanded grant drawer (Investments → Equity Compensation) showing a vested
  tranche
- WHEN I tap "Undo"
- THEN a confirm dialog "Undo this vest?" opens as a modal (never a second inline button —
  the timeline's action column is 84px wide inside a horizontally-scrolled panel on a
  phone, and an inline confirm was clipped off-screen there, 2026-09-22) and NO API call
  has been made yet.
- WHEN I tap "Undo vest"
- THEN a POST to /api/equity/vest-events/{vest_event_id}/undo is made (the previous
  handler POSTed to a non-existent /tranches/{id}/vest_undo route and swallowed the 404),
  the row shows "Undoing…", the tranche flips back to upcoming, the lot and income txn
  are reversed, and the grants view refreshes.

### EQ-UNDO-CANCEL
- GIVEN the "Undo this vest?" dialog is open
- WHEN I tap "Cancel"
- THEN the dialog closes, the row still reads "Undo", and no API call is made.

### EQ-UNDO-409
- GIVEN a vested tranche whose lot was already disposed
- WHEN I tap Undo → Undo vest
- THEN the backend returns 409 and an inline error appears under the row: "Cannot undo —
  the lot has already been sold or disposed." Nothing changes in the grant state.

### EQ-EDIT-GRANT
- GIVEN the expanded grant drawer
- WHEN I tap "Edit grant" (beside "Delete grant")
- THEN a modal opens prefilled with the grant's date, price at grant, tax event, share
  delivery (release_required), account, and note; instrument, type and total units are
  not editable.
- WHEN I switch Share delivery to "Delivered later" and save
- THEN a PATCH to /api/equity/grants/{id} carries `release_required: true`, the drawer
  refreshes, and only vests recorded AFTER the change follow the two-step flow — vests
  already on the books keep their Released state.
