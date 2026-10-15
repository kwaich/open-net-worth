# Acceptance criteria — Investments: Bonds polish

**Lane:** `lane/bonds` (investments-redesign/05-bonds.md)
**Status:** implemented

---

## Holdings grouped by asset class — bond columns + maturity card

**GIVEN** I hold a coupon bond (SSB or market) and a bond ETF (no maturity date)
**WHEN** I open the Investments tab → Holdings sub-tab grouped by asset class
**THEN** the Bonds section shows the bond-specific columns (Type / Coupon / Matures / Face)
  AND the "Upcoming maturities & coupons" card is visible
  AND the maturity ladder lists only the dated bond (SSB/market)
  AND a footnote reads "1 bond fund without a fixed maturity is not shown above"

---

## Holdings grouped by account — inline bond meta badge

**GIVEN** I hold a bond (e.g. a market bond at 4.25% maturing 2029)
**WHEN** I view Holdings grouped by account (non-bondColumns layout)
**THEN** the bond's name cell shows a compact inline badge reading "4.25% · mat 2029"

**GIVEN** I hold a bond ETF (no maturity date) in a non-bondColumns layout
**THEN** its name cell shows "· fund" in the inline badge

---

## Bond funds footnote — empty-state scenario

**GIVEN** I hold only bond ETFs (no maturity date on any holding)
**WHEN** I open the Holdings Bonds section
**THEN** the maturity card shows "No maturities in the next 12 months."
  AND a footnote counts the excluded bond funds

---

## Maturity card empty-state (no near-term maturities)

**GIVEN** I hold dated bonds that mature more than 12 months away
**WHEN** the maturity card renders
**THEN** the empty-state body reads "No maturities in the next 12 months."
  AND no footnote appears (ladder_excluded_count == 0)

---

## Maturity card error and loading states

**GIVEN** the fixed-income API call fails
**WHEN** the BondMaturityCard mounts
**THEN** it shows the error state with "Couldn't load fixed-income maturity data."

**GIVEN** the data is loading
**THEN** the loading state is shown

---

## Bond section swatch color

**GIVEN** the Bonds section header is visible (either in the allocation bar or the Holdings section swatch)
**THEN** the swatch color is teal `#2ba39a`
  AND the lifestyle category color remains `#34b08a` (unchanged)

---

## Coupon reminder scheduled task

**GIVEN** I acquire a coupon-paying bond (market / SSB / FD with coupon_freq_months set)
**WHEN** I open the Scheduling (Automations) section
**THEN** a "Bond coupon reminder" task exists firing daily
  AND acquiring a second coupon bond does NOT create a second reminder task

**GIVEN** I acquire a T-bill (zero-coupon) or a bond ETF
**THEN** no coupon_reminder task is created

---

## No bonds held

**GIVEN** my portfolio contains no bond holdings
**WHEN** the BondMaturityCard checks `count === 0`
**THEN** the card does not render at all (null)
