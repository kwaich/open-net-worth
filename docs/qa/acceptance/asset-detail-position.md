# Acceptance — Asset Detail: Position subtab

A third subtab, **Position**, appears next to Overview / Analysis **only when the asset is held**.
It surfaces the holding's summary plus the full acquisition-lots and realized-history tables
(moved out of the cramped right rail). Backend exposes `position_id` on the position block so the
tables can fetch lots/realized for the exact position.

Context: `frontend/src/components/intel/PositionSubtab.tsx` + `AssetDetailView.tsx`, reusing
`frontend/src/components/shared/LotsPanel.tsx`. Backend: `backend/services/asset_detail.py`.

**Multi-position (custodian model) + crypto base currency** — the same instrument can be held
at more than one account (custodian model: one securities account per broker); the position block
aggregates ALL of that instrument's liquid positions instead of showing only the first one, and
also surfaces unvested (locked-bucket) exposure from an equity grant. Crypto's headline price and
chart always show in the portfolio's base currency — never a per-holding cost-currency preference.

### ADPOS-TAB-ALWAYS
- GIVEN I open **any** asset detail page (held or not)
- WHEN the page loads
- THEN a **Position** tab always appears after Overview and Analysis

### ADPOS-TAB-NOT-HELD
- GIVEN I open an asset detail page for an asset I do **not** hold
- WHEN I click the **Position** tab
- THEN it shows an empty state ("No position in this asset — you don't currently hold
  &lt;ticker&gt;. Record your first acquisition to start tracking it here.") with an **Add lot**
  button, and the right rail shows a "You don't hold &lt;ticker&gt;" card instead of
  the position summary

### ADPOS-SUMMARY
- GIVEN I am on the Position tab of a held asset
- THEN a summary strip shows five cells — **Quantity, Avg cost, Market value, Unrealized P&L,
  Realized P&L** — with Unrealized/Realized P&L coloured green/red by sign

### ADPOS-LOTS
- GIVEN I am on the Position tab
- WHEN I view the lots panel
- THEN the **Acquisition lots** table lists the open lots (Acquired · Original · Remaining ·
  Unit cost · Fees · Kind · Term · Unrealized), and the **Realized history** toggle shows
  disposals with a "Total realized" footer — both fetched for **this** position only

### ADPOS-RAIL-TRIM
- GIVEN I am on a held asset's detail page
- THEN the right-rail "Your Position" card shows only the summary rows (no inline TAX LOTS list)
  and a faint line: "Full acquisition & realized lots are in the Position tab."

### ADPOS-RAIL-OPENS
- GIVEN I am on a held asset's detail page (any tab)
- WHEN I click the right-rail "Your Position" card
- THEN the Position subtab opens (it's keyboard-activatable too — Enter/Space when focused)

### ADPOS-UNHOLD-EMPTY
- GIVEN I am on the Position tab and the holding becomes fully disposed (a refetch returns not-held)
- THEN the Position tab stays selected and switches to the "No position" empty state (the tab no
  longer disappears)

### ADPOS-NONOVERVIEW-HIDES-PRICE
- GIVEN I switch to the Position tab
- THEN the large price block and the price chart (Overview-only) are hidden, exactly as on Analysis

---

## Multi-account aggregation (custodian model)

### ADPOS-MULTI-AGGREGATE
- GIVEN I hold the SAME instrument at two different custodian accounts (e.g. 10 shares at IBKR,
  5 at Saxo)
- WHEN I open that asset's detail page
- THEN the Quantity/Market value/Weight figures reflect the SUM of both positions (15 shares,
  combined value), not just the first account's 10 shares

### ADPOS-MULTI-PER-ACCOUNT-TABLE
- GIVEN I hold an instrument at more than one account
- WHEN I open the Position tab
- THEN a "Held at" table appears listing each contributing account by name with its own
  Quantity, Market value, Cost basis, and Unrealized P&L — with NO venue sublabel anywhere else
  on the page (the rail card and summary strip never list account names, only the breakdown table)

### ADPOS-MULTI-SINGLE-ACCOUNT-NO-TABLE
- GIVEN I hold an instrument at exactly one account
- WHEN I open the Position tab
- THEN the "Held at" table does NOT appear (it only shows for a genuinely multi-account holding)

### ADPOS-MULTI-MIXED-CURRENCY-COST
- GIVEN my two contributing positions were bought in different currencies (e.g. USD and SGD)
- WHEN I view the Avg cost cell
- THEN it shows "—" (unknown) rather than a fabricated mixed-currency number — the per-account
  table still shows each account's own correct native cost and currency, and Unrealized P&L
  at the top level is still a real number (each leg converted to base individually)

### ADPOS-MULTI-NO-SINGLE-LOTS
- GIVEN I hold an instrument at more than one account
- WHEN I look at the lots/realized area of the Position tab
- THEN it shows a note that lots & realized history are per-account (shown in the breakdown
  table above) instead of silently fetching only one account's lots

---

## Unvested (equity grant) exposure

### ADPOS-UNVESTED-SHOWS-ALONGSIDE-LIQUID
- GIVEN I hold vested (liquid) shares of an instrument AND have unvested RSU units from a grant
  on the same instrument
- WHEN I open the asset detail page
- THEN the "Your Position" rail card shows both the vested Quantity AND a separate "Unvested"
  line with its own unit count and market value — the unvested units are never added into the
  sellable Quantity figure

### ADPOS-UNVESTED-NEXT-VEST
- GIVEN my grant has an upcoming (not-yet-vested) tranche
- WHEN I view the Unvested section
- THEN it shows the next vest date and unit count

### ADPOS-UNVESTED-GRANT-LINK
- GIVEN the Unvested section is showing
- WHEN I click "View grant in Equity Comp"
- THEN I land on the Investments tab with the Equity Comp sub-tab selected

### ADPOS-UNVESTED-LOCKED-ONLY
- GIVEN I have ONLY unvested units for an instrument (no vested shares yet)
- WHEN I open that asset's detail page
- THEN the page shows the holding as held (with 0 sellable quantity) and the Unvested section —
  it never falls back to "You don't hold this asset"

### ADPOS-UNVESTED-ABSENT
- GIVEN an instrument has no locked/grant position at all
- WHEN I view its asset detail page
- THEN no Unvested section appears anywhere

---

## Crypto base currency (headline + chart)

### ADPOS-CRYPTO-BASE-ALWAYS
- GIVEN my portfolio base currency is SGD and I hold a coin I originally bought via a
  USD-denominated account
- WHEN I open that coin's asset detail page (from the holding, from search, or from the
  watchlist)
- THEN the headline price AND the chart both show in SGD (the portfolio base currency) — never
  in USD (the account's cost currency), regardless of which holding/account I opened it from

### ADPOS-CRYPTO-HEADLINE-CHART-AGREE
- GIVEN a crypto asset detail page is open
- WHEN I compare the big headline price to the chart's y-axis / tooltip currency
- THEN they always show the SAME currency — never a mismatch

### ADPOS-CRYPTO-BASE-UNSUPPORTED-FALLBACK
- GIVEN the portfolio base currency is one CoinGecko does not quote that coin in
- WHEN I open the asset detail page
- THEN the headline and chart fall back to the coin's quote currency (e.g. USD) instead of
  erroring or showing blank

---

## Manual lots and sales (quote Position tab)

### ADPOS-LOT-ADD
- GIVEN I am on the Position tab of a held asset
- WHEN I click **+ Add lot**, fill date / quantity / unit cost, and save
- THEN a new acquisition row appears in Acquisition lots with that date and quantity

### ADPOS-LOT-EDIT-BUY
- GIVEN I am on the Position tab with at least one fully-open buy lot
- WHEN I click that buy row, change the quantity, and save
- THEN the modal title is "Edit lot — &lt;ticker&gt;" and the row's Original and Remaining
  both show the new quantity after save

### ADPOS-LOT-EDIT-SALE
- GIVEN I am on the Position tab with at least one realized sale
- WHEN I click that sale row, change the proceeds per unit, and save
- THEN the modal title is "Edit sale — &lt;ticker&gt;" and the realized amount updates

### ADPOS-LOT-REMOVE
- GIVEN I am on the Position tab with a fully-open lot
- WHEN I open the lot, click **Remove lot**, and confirm
- THEN the lot disappears from Acquisition lots

### ADPOS-LOT-FIRST-HOLD
- GIVEN I open the Position tab of a ticker I have never held
- WHEN I click **Add lot**, pick a holding account, enter quantity and cost, and save
- THEN the empty state is replaced by a Position summary and the new lot

### ADPOS-LOT-SKIP-CASH
- GIVEN I am adding a lot or recording a sale
- WHEN I leave the funding / proceeds account on "don't add / don't deduct"
- THEN the form shows a warning that cash will not move, and saving still records the lot or sale

### ADPOS-LOT-MULTI-ACCOUNT
- GIVEN I hold the same ticker at two accounts
- WHEN I click **+ Add lot**
- THEN the Add lot form opens immediately with an inline **Account** selector (including
  **+ New account…**), not a separate "Which account?" step, and saving records against the
  chosen account
- WHEN I click **+ Record sale**
- THEN a "Which account?" picker appears before the sale form

