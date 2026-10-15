# Investments — Activity Sub-Tab Acceptance Criteria

**RFC:** `docs/rfc/investments/04-activity.md`
**Lane:** `lane/ui-activity`

## Criteria

### ACT-1 — Chronological ledger with type pills

GIVEN a portfolio with buys, sells, dividends, a bond coupon, a deposit, a vest, and a fee recorded,
WHEN I open Investments → Activity,
THEN I see one chronological ledger (newest first) with a colored type-pill per row and signed base-currency amounts.

Expected pill colors: Buy=accent-blue, Sell=muted, Dividend/Coupon/Interest=green, Deposit/Withdrawal=teal, Vest=violet, Fee=red.

### ACT-2 — Type filter chip narrows rows

GIVEN the loaded Activity tab,
WHEN I click the "Income" filter chip,
THEN only Dividend/Coupon/Interest rows remain and the "All" chip is deselected.
The groups are All / Buys / Sells / Income / Transfers (deposit, withdrawal, transfer) /
Vests / Fees.

### ACT-3 — Free-text search narrows rows

GIVEN the loaded Activity tab,
WHEN I type a ticker (e.g. "NVDA") into the search box,
THEN only rows matching symbol/name/detail/account remain.
AND when I enter a query that matches nothing, the table shows "No matching activity." instead of blank rows.

### ACT-4 — Realized G/L callout deep-links to Tax

GIVEN realized gains exist this year (non-empty /api/realized-pnl),
WHEN I view the Activity tab,
THEN a read-only "Realized G/L · YTD" callout appears showing the total and long/short breakdown.
AND clicking the "Tax tab" link navigates to the Tax tab (not re-computing tax in place).

### ACT-5 — Zero-activity empty state

GIVEN no investment transactions of any kind exist,
WHEN I open Investments → Activity,
THEN I see a "No activity yet." empty state, not a blank or broken table.

### ACT-6 — Vest rows are unsigned

GIVEN a vest event appears in the ledger,
WHEN I look at the Amount column for that row,
THEN the vest row shows the FMV amount without a +/− sign (informational shares, no cash movement)
AND the amount is styled in muted (not good or bad).
The renderer keys this off the row's `signed: false`, the same flag a transfer carries.

### ACT-7 — 30-day summary cards

GIVEN the Activity tab is loaded,
WHEN I view the SummaryBar at the top,
THEN I see four cards: "Activity · 30d" (count), "Net invested · 30d", "Income · 30d", "Fees · 30d"
AND vest events do NOT contribute to any of these summary totals — in particular a vest's FMV is
NOT "Income · 30d" (shares were delivered, no cash was received; only dividend / coupon / interest
count) and a transfer contributes nothing to any of them.

### ACT-8 — Phone toolbar: one line, chips merged into a filter button

GIVEN the Activity tab on a phone-width viewport (< 640px),
WHEN I look at the ledger card's toolbar,
THEN the type-chip row is NOT shown; instead search, the date-range dropdown and a filter icon
button share ONE line, with search first and filling the leftover width
AND tapping the filter button opens the app's standard bottom sheet ("Filter activity") listing
All / Buys / Sells / Income / Transfers / Vests / Fees
AND choosing one closes the sheet, narrows the rows, and marks the button with the active dot
AND tapping the date-range button opens the SAME standard bottom sheet ("Date range") listing
Today / Past 7–90 days / Month-to-date / Year-to-date / the six most recent quarters, the current
range ticked; choosing one closes the sheet, re-scopes the rows and relabels the button — the
anchored dropdown menu never floats over the cards on a phone.
On a wide layout the chip row, the anchored range menu and the table are unchanged, and the
filter button is not rendered.

### ACT-9 — Phone rows are cards, never a sideways scroll

GIVEN the Activity tab on a phone-width viewport,
WHEN I look at the rows,
THEN each event is a two-line card — symbol + type pill and the signed amount on line one, the
trade detail and "date · account" on line two — with no horizontal scrolling of the card or the page
AND an event with no symbol leads with its name; with neither the pill alone leads.

### ACT-10 — A vest names its instrument and account

GIVEN a vest event whose grant is for NET and whose struck lot sits in "Taxable Brokerage",
WHEN I view it on the Activity tab (table or phone card),
THEN the row reads `NET · Vest · <units> units @ FMV …` with account "Taxable Brokerage" — the
instrument is resolved from the GRANT and the account from the struck LOT (a vest event stores
neither itself; the grant's own account_id is only the fallback for a not-yet-released tranche)
AND filtering Activity by that account keeps the vest rows.

### ACT-11 — A vest is one row, never also a Buy

GIVEN an RSU release that delivered 488.75 KSTL at FMV 37.75 EUR (which mints a `kind=grant` lot),
WHEN I view the Activity tab,
THEN that day shows ONE KSTL row — the Vest — and no "KSTL Buy 488.75 @ 37.75 EUR −S$27k"
AND "Net invested · 30d" does not count the delivered shares as money spent.
Only `buy` (and DRP) lots are purchases; `grant` and `transfer_in` lots moved no cash.

### ACT-12 — Shares moved between my accounts are ONE Transfer row, not a $0 Sell

GIVEN 1242 KSTL moved from a workplace custodian to IBKR, recorded as a non-taxable disposal
(`is_taxable=false`, zero proceeds, basis carried over, no gain booked) that names its
destination (`transfer_to_account_id` = IBKR, by id or exact name),
WHEN I view the Activity tab,
THEN that day shows ONE row, `KSTL · Transfer · 1242 units → IBKR`, at the source account with a
dash in the Amount column — never "Sell 1242 @ 0 EUR +S$0.00", and never a second "Transfer in"
row (direction is not a type: a move between my own accounts is one event)
AND a non-taxable disposal with NO destination (a gift / write-off / pre-feature move) reads
`1242 units transferred out` under the same Transfer pill
AND it files under the Transfers filter (not Sells), and contributes nothing to "Net invested · 30d"
AND filtering by EITHER the source account or IBKR keeps the row
AND `GET /api/investments/activity?type=transfer` returns exactly those rows; `type=transfer_out`
is a 422 (no such type).
A taxable sale is unchanged (the choke refuses a taxable $0 sale, so `is_taxable=false` is the only
$0 path); a destination on a taxable sale, on a non-zero price, or beside `proceeds_account_id`
is refused before any write ("a transfer moves shares, not money"), as is the position's own
account.

### ACT-13 — The Sell form offers "Moved to" for a transfer

GIVEN the Record sale form for a holding,
WHEN I tick "Not a sale — transferring out…",
THEN the "Deposit proceeds to" picker is replaced by a "Moved to" picker whose default is
"Not one of my accounts — gift or write-off" and whose options are my asset accounts EXCEPT the
one holding this position
AND the primary button reads "Record transfer" (not "Record sale"); picking an account posts
`transfer_to_account_id`, and the success state is titled "Transfer recorded" and reads
"Moved to <account> — basis carried over, no gain booked."
AND the Lucius confirm card for the same move is titled "Record transfer" with a "moved to" row
showing the account NAME (the id is never what the user approves).

## Screenshot states required (UI Quality Gate)

1. Loaded ledger with all type-pills visible (all 9 types represented)
2. "Income" filter chip active — only green-pill rows showing
3. Ticker search active with non-empty query
4. Empty search result — "No matching activity." row
5. Zero-activity empty state (SubEmpty / EmptyState)
6. Loading skeleton state (during fetch)
7. Error state (fetch failed)
8. Phone (390px): card list + one-line toolbar (ACT-9)
9. Phone: "Filter activity" bottom sheet open (ACT-8)
10. Phone: a filter applied — active dot on the button, narrowed cards
11. Phone: "Date range" bottom sheet open with the current range ticked (ACT-8)
