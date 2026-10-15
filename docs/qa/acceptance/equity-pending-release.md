# Acceptance — Two-step vest: shares vested but not yet released

Feature: a grant whose plan DELIVERS shares some time after they vest (settlement lag,
blackout, country delivery rules). Vested units are owned and counted in net worth, but are
NOT sellable until the release is recorded. ADR `2026-07-31-two-step-vest-pending-release`.

**Opt-in.** Every criterion below applies only to a grant created with delivery set to
"Delivered later". `REL-OPTOUT-*` guards the default path for everyone else.

Location: Investments → Equity sub-tab.

---

## Setup

### REL-SETUP-CREATE
- GIVEN I am on the Equity sub-tab with no grants
- WHEN I click "Add grant", fill the required fields, set **Share delivery** to
  "Delivered later — released after vest", and submit
- THEN the grant appears in the list, and re-opening it shows the delivery choice persisted

---

## The default path must not change

### REL-OPTOUT-DEFAULT
- GIVEN I am creating a grant
- WHEN I open the "Add grant" modal
- THEN **Share delivery** defaults to "Sellable at vest — the usual case", and nothing about
  the form is otherwise different from before this feature existed

### REL-OPTOUT-VEST
- GIVEN a grant with delivery "Sellable at vest" and an unvested tranche
- WHEN I mark that tranche vested
- THEN the success message says units moved to liquid, there is **no** "Expected release
  date" field anywhere in the vest form, and the "Awaiting release" panel does not appear

---

## Vesting into the pending state

### REL-VEST-FIELD
- GIVEN a "Delivered later" grant with an unvested tranche
- WHEN I open "Mark vested" for that tranche
- THEN an **"Expected release date"** field is shown, empty, and its help text says it is
  optional

### REL-VEST-PREVIEW
- GIVEN the "Mark vested" form for a "Delivered later" grant, with units and FMV filled in
- WHEN the preview block renders
- THEN it reads "locked → awaiting release" (not "locked → liquid") and shows a
  **"Not sellable until released"** badge

### REL-VEST-CONFIRM
- GIVEN the completed "Mark vested" form for a "Delivered later" grant
- WHEN I click "Confirm vest"
- THEN the success screen says the units are **awaiting release** and explains they count in
  net worth but are not sellable yet

### REL-VEST-TRANCHE-BADGE
- GIVEN I have just vested a tranche on a "Delivered later" grant
- WHEN I open that grant's detail drawer
- THEN the tranche shows an **"Awaiting release"** badge, offers **no** "Mark vested" button
  (it has already vested), and still offers "Undo"

---

## The Awaiting-release panel

### REL-PANEL-HIDDEN
- GIVEN no grant has any undelivered vested units
- WHEN I view the Equity sub-tab
- THEN there is **no** "Awaiting release" panel at all (not an empty-state card)

### REL-PANEL-SHOWS
- GIVEN a vest is awaiting release
- WHEN I view the Equity sub-tab
- THEN an "Awaiting release" panel lists it with symbol, unit count, the vest date, and
  either the expected release date or "no expected date"

### REL-PANEL-OVERDUE
- GIVEN a pending release whose **expected** release date is in the past
- WHEN I view the "Awaiting release" panel
- THEN that row carries an **Overdue** badge
- AND a pending release with NO expected date does **not** carry one

---

## Recording the release

### REL-MARK-OPEN
- GIVEN a row in the "Awaiting release" panel
- WHEN I click "Mark released"
- THEN a modal opens, pre-filled with today's date, stating that nothing about the money
  changes and naming the vest-date price the lot will keep

### REL-MARK-FLOOR
- GIVEN the "Mark released" modal
- WHEN I set the release date to a date BEFORE the vest date
- THEN "Confirm release" is disabled and the text "Shares can't be delivered before they
  vest." is shown

### REL-MARK-CONFIRM
- GIVEN the "Mark released" modal with a valid date
- WHEN I click "Confirm release"
- THEN the modal confirms the units are now sellable holdings at the vest-date cost basis,
  and explicitly states that no transaction was written
- AND on closing it, the row is gone from "Awaiting release"
- AND the grant's tranche badge has changed from "Awaiting release" to "Vested"

### REL-MARK-NO-TXN
- GIVEN I have just recorded a release
- WHEN I go to the Accounts tab and look at recent transactions
- THEN **no new transaction** was created by the release (the only one is the income
  transaction dated the VEST date, written when the vest was recorded)

---

## Net worth honesty

### REL-NETWORTH-BAND
- GIVEN units awaiting release
- WHEN I view the Overview net-worth breakdown
- THEN their market value appears in the **Contingent** band, not Liquid
- AND after recording the release, that value has moved to **Liquid**

### REL-HOLDINGS-NOT-SELLABLE
- GIVEN units awaiting release
- WHEN I view the Holdings/Investments list
- THEN there is no sellable (liquid) position row claiming those units
