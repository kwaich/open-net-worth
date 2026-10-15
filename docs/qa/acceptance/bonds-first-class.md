# Acceptance — Bonds / Fixed income (first-class)

The QA contract for first-class bond support (RFC `.claude/rfcs/bonds-first-class.md`,
ADR 0020). Interaction-level: each criterion performs the action and asserts the observable
outcome. Precondition for the holdings/income criteria: at least one bond holding exists
(seed via `update_instrument` → `asset_class="bond"` + `bond_terms`, then a position; or the
QA seed fixture).

## Backend / valuation

### FI-VALUE-SSB-PAR
- GIVEN an SSB instrument (`bond_terms.subtype="ssb"`, face 1, qpu 0.01) with a position qty 20000
- WHEN I read the portfolio / `GET /api/fixed-income`
- THEN the holding's mark is 100 and its value is 20000 in base currency (par redemption), independent of today's date

### FI-VALUE-TBILL-ACCRETES
- GIVEN a T-bill (`subtype="tbill"`, `issue_price=99`, issue 2026-01-01, maturity 2026-12-31)
- WHEN its mark is computed at issue vs. at maturity
- THEN it is ≈99 at issue and ≈100 at maturity, rising monotonically in between (linear accretion)

### FI-VALUE-FD-ACCRUES
- GIVEN a fixed deposit (`subtype="fd"`, `coupon_rate_annual=0.0365`, 1-year term)
- WHEN its mark is computed mid-term and after maturity
- THEN value = principal + simple interest accrued to date, capped at the full term after maturity

### FI-FORECAST-COUPON
- GIVEN a market bond (face 1000, coupon 4%, qty 10)
- WHEN I read `GET /api/income/forecast`
- THEN the holding contributes 400/yr with `source="coupon"`; a T-bill contributes its annualized discount with `source="accretion"`

### FI-API-SHAPE
- GIVEN bonds are held
- WHEN I `GET /api/fixed-income`
- THEN status 200 and the body has `total_base`, `count`, `by_subtype`, `maturity_buckets{d30,d90,y1}`, `maturity_ladder[]`, and per-holding `next_coupon`

### FI-UPDATE-TERMS
- GIVEN an equity instrument
- WHEN I `update_instrument` with `asset_class="bond"` + `bond_terms`
- THEN the terms persist (clamped/coerced); demoting back to a non-bond clears `bond_terms` to null

## Frontend (UI Quality Gate — A–K)

### FI-UI-A-SECTION
- GIVEN the portfolio contains a bond holding
- WHEN I open Investments → Holdings grouped by asset class
- THEN a "Fixed income" section appears (teal-green dot), ordered after Equities, with a count badge and subtotal

### FI-UI-B-COLUMNS
- GIVEN the Fixed income section is visible
- WHEN I read its table header
- THEN bond columns (Type / Coupon / Matures / Face) replace Qty/Cost/Price/Cur; Value, Wt%, P&L remain

### FI-UI-C-BADGE
- GIVEN bonds of different subtypes
- WHEN I view their rows
- THEN each shows its type badge: SSB (positive), T-bill / Market / FD (neutral), ETF (muted); a zero-coupon T-bill shows "Zero-coupon" in the Coupon column

### FI-UI-D-MATURITY-CARD
- GIVEN bonds with a maturity date are held
- WHEN the Holdings sub-tab loads
- THEN an "Upcoming maturities & coupons" card renders below the Fixed income table (open on desktop, collapsed <640px)

### FI-UI-E-BUCKETS
- GIVEN one bond matures within 90 days
- WHEN the maturity card is open
- THEN "Matures ≤90d" shows a non-zero base-currency amount; "≤30d" shows — when none are that close

### FI-UI-F-EMPTY
- GIVEN all bond holdings are ETFs (no maturity date)
- WHEN the maturity card is open
- THEN its empty state renders: "No maturities in the next 12 months."

### FI-UI-G-INCOME-BYKIND
- GIVEN coupon income recorded as `interest` plus some dividends exist
- WHEN I open Investments → Income
- THEN a "By income type" block lists Dividends / Interest / Distributions with amounts; Interest is present and non-zero

### FI-UI-H-RECORD-INTEREST
- GIVEN a bond holding row
- WHEN I click its income/record action
- THEN `RecordDividendForm` opens with Type defaulting to "Interest" (not "Dividend")

### FI-UI-I-ALLOCATION-BAND
- GIVEN bonds are held
- WHEN I view the Overview net-worth chart / Allocation donut
- THEN a teal-green "Fixed income" band is shown, distinct from the Equities band (bonds no longer fold into equity)

### FI-UI-J-FILTER
- GIVEN the Holdings class filter is open
- WHEN I select "Fixed income"
- THEN only bond rows remain visible

### FI-UI-K-CHAT-CLEARANCE
- GIVEN the chat panel is open
- WHEN I view the Fixed income section and maturity card
- THEN all content stays fully visible and the card's collapse toggle is clickable (no overlap)
