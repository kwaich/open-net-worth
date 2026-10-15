# Acceptance criteria — Investments › Income sub-tab (lane/income)

GIVEN/WHEN/THEN acceptance criteria for the income polish lane. These map to the
`make qa-e2e` / `make ui-gate` gate and the test IDs referenced by the orchestrator.

---

## INC-01 Contributors collapse (top-8)

GIVEN a portfolio with 9 or more income-generating holdings
WHEN I open Investments › Income
THEN the "Where your income comes from" panel shows the top 8 contributors
AND a single aggregate "+N more holdings" row appears below them (muted/greyed)
AND a "Show all N holdings" button is visible beneath the list.

## INC-02 Contributors expand

GIVEN the contributors panel is collapsed (INC-01 precondition)
WHEN I click "Show all N holdings"
THEN all N holdings are revealed (the "+N more" row disappears)
AND the button text changes to "Show top 8 only"
WHEN I click "Show top 8 only"
THEN the list collapses back to 8 + the aggregate row.

## INC-03 Bond coupon source → green dot

GIVEN I hold a coupon bond with a fixed annual coupon rate
WHEN I open Investments › Income
THEN the bond appears in "Where your income comes from" with a GREEN dot (not grey)
AND the dot tooltip reads "Bond coupon" (not "Trailing 12M").

## INC-04 Bond coupon in upcoming-payments calendar

GIVEN I hold a coupon bond with a coupon date within the next 90 days
WHEN I open Investments › Income › "Upcoming payments"
THEN the coupon date appears as a row with a green (scheduled) dot
AND the row's "Holding" cell shows the bond symbol/name (no chip for coupon rows).

## INC-05 Principal-at-maturity chip on final coupon

GIVEN I hold a bond maturing within 90 days
WHEN I view the upcoming-payments calendar
THEN the final coupon row shows a "Principal at maturity" chip next to the amount.

## INC-06 Principal row at maturity

GIVEN I hold a bond maturing within 90 days
WHEN I view the upcoming-payments calendar
THEN a separate "Principal" row appears at the maturity date
AND the "Holding" cell of that row shows a "Principal" chip
AND the amount reflects face × quantity in the base currency
AND the principal amount is counted in the panel's total.

## INC-07 Calendar collapse (top-8)

GIVEN 9 or more payments are upcoming within the horizon
WHEN I open the upcoming-payments panel
THEN only the first 8 rows are shown
AND a "Show all N payments" button appears below the table.

## INC-08 Calendar expand

GIVEN the calendar is collapsed (INC-07 precondition)
WHEN I click "Show all N payments"
THEN all N payment rows are visible
AND the button text changes to "Show fewer"
WHEN I click "Show fewer"
THEN the table collapses back to 8 rows.

## INC-09 Empty state

GIVEN a portfolio with no income-generating holdings and no recorded income
WHEN I open Investments › Income
THEN the empty state ("No income yet") is shown
AND no summary cards, contributor list, or calendar appear.

## INC-10 Loading state

GIVEN the income API is slow to respond
WHEN I open Investments › Income
THEN a loading indicator ("Loading income…") is shown while data is in-flight.

## INC-11 Error + retry state

GIVEN the income API returns an error
WHEN I open Investments › Income
THEN an error message ("Couldn't load income data.") is shown
AND a retry button is available that re-fires all three income requests.

### INC-12 Recent income includes every recorded income kind

- GIVEN recorded dividend, interest, and distribution events
- WHEN I open Investments → Income
- THEN a "Recent income" section appears below "Upcoming payments", all three event kinds
  are represented rather than filtering the list to dividends, and the rows are ordered
  newest payment first.

### INC-13 Recent income shows delivery and net amount

- GIVEN one cash income event and one reinvested (DRP) income event
- WHEN I view Recent income
- THEN each row shows the payment date, holding label, income kind, and net base-currency
  amount; when FX is unavailable it shows the native net amount with an explicit warning
  instead of mislabelling a 1:1 fallback as base currency; the cash event says "Cash" and
  the DRP event says "Reinvested"
- AND the Net and Method headers use the same right alignment as their values.

### INC-14 Recent income collapse and expansion

- GIVEN 9 or more recorded income events
- WHEN I open Investments → Income and click the "Show all N income events" control
- THEN the initial list of 8 newest rows expands to every recorded event and the control
  changes to "Show top 8 only".

### INC-15 Recent income is usable on a phone

- GIVEN nine recorded income events on a phone-width viewport
- WHEN I view and expand Recent income
- THEN date, holding, net amount, and Cash/Reinvested delivery remain readable without
  horizontal page scrolling and the Show all control remains tappable.
