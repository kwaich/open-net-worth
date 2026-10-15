# Acceptance criteria — Investments / Holdings sub-tab (Wave 2)

Interaction-level GIVEN/WHEN/THEN for the redesigned **Holdings** sub-tab: cockpit fold,
card/table density toggle with persistence, inline 100%-total target editor (balanced vs
unbalanced), Sector|Region health toggle with region >70% flag and OK/N-flags badge,
configurable stress/guideline, Today's-movers row, and responsive collapse at ≤1020 px.

The QA agent must exercise every criterion and report PASS/FAIL per ID. Run against a QA
instance on ports 8090–8099 with `ONW_QA_MODE=1`. Seed at least 4 positions in 2+
asset classes with allocation targets set.

---

## Cockpit fold

### INV-H-COCKPIT-LOAD
- GIVEN the Holdings sub-tab is active and positions exist
- WHEN the sub-tab renders
- THEN a cockpit summary fold is visible above the holdings table, showing Total Value,
  Today's change (absolute + %), Unrealized P&L, and % of Net Worth — all populated, no "—"
  for any field when a priced position exists.

### INV-H-COCKPIT-FOLD-TOGGLE
- GIVEN the cockpit fold is visible
- WHEN I click the fold/collapse chevron on the cockpit
- THEN the cockpit collapses to a single slim row (showing only Total Value and Today %)
  and the holdings table fills the freed vertical space.

### INV-H-COCKPIT-FOLD-EXPAND
- GIVEN the cockpit fold is collapsed
- WHEN I click the expand chevron
- THEN the full cockpit stats rows are restored.

### INV-H-COCKPIT-FOLD-PERSISTS
- GIVEN I collapse the cockpit fold
- WHEN I navigate away to another tab and return to Holdings
- THEN the cockpit remains collapsed (the collapsed state was persisted in localStorage).

---

## Card / table density toggle

### INV-H-DENSITY-DEFAULT
- GIVEN the Holdings sub-tab loads for the first time (no localStorage key set)
- WHEN I view the toolbar
- THEN the density toggle defaults to "Table" view (not Card view).

### INV-H-DENSITY-CARD
- GIVEN I am on the Holdings sub-tab in Table view
- WHEN I click the "Card" option in the density toggle
- THEN the holdings table is replaced by a card grid — each holding is a card showing
  ticker, name, value, day change, and unrealized P&L; the table rows are no longer visible.

### INV-H-DENSITY-TABLE
- GIVEN I am on the Holdings sub-tab in Card view
- WHEN I click the "Table" option in the density toggle
- THEN the card grid is replaced by the holdings table; no card elements remain.

### INV-H-DENSITY-PERSISTS
- GIVEN I switch to Card view
- WHEN I reload the page and navigate to the Holdings sub-tab
- THEN Card view is still active (persisted in localStorage across page loads).

### INV-H-DENSITY-SEARCH-APPLIES
- GIVEN I am in Card view and type a ticker into the search field
- WHEN the search filter matches a subset of holdings
- THEN only cards matching the query are visible; all other cards are hidden.

---

## Inline 100%-total target editor

### INV-H-TARGET-EDITOR-OPEN
- GIVEN allocation targets have been set for some holdings
- WHEN I click the "Edit targets" (or equivalent) button on the Holdings toolbar
- THEN an inline target editor expands within the holdings table, showing a target % input
  for each row and a running total.

### INV-H-TARGET-BALANCED
- GIVEN the inline target editor is open and all target % values sum to exactly 100
- WHEN I look at the total indicator
- THEN the total reads "100%" and is styled as balanced/OK (no warning color, no error text).

### INV-H-TARGET-UNBALANCED
- GIVEN the inline target editor is open and the sum of target % inputs is NOT 100 (e.g. 85 or 110)
- WHEN I read the total indicator
- THEN the total displays the actual sum (e.g. "85%" or "110%") styled in a warning/error
  tone, and a message such as "Targets must sum to 100%" is shown; the Save button is disabled
  or shows a warning.

### INV-H-TARGET-SAVE
- GIVEN the inline target editor shows a balanced total (sum = 100%)
- WHEN I click Save
- THEN the editor closes, and the allocation drift bars below each holding row update to
  reflect the new targets (drift = current weight − target weight).

### INV-H-TARGET-CANCEL
- GIVEN the inline target editor is open with unsaved changes
- WHEN I click Cancel (or press Escape)
- THEN the editor closes without saving; the previous target values are intact.

---

## Sector | Region toggle and health badge

### INV-H-SECTOR-REGION-TOGGLE
- GIVEN the Holdings sub-tab is active with positions that have sector/region metadata
- WHEN I click the "Sector" pill in the Sector | Region toggle
- THEN holdings are grouped (or annotated) by sector; the active toggle shows "Sector"
  selected.
- WHEN I click the "Region" pill
- THEN holdings are grouped/annotated by region; the active toggle shows "Region" selected.

### INV-H-REGION-70-FLAG
- GIVEN I have switched to Region view and one region exceeds 70% of portfolio weight
- WHEN I view the region breakdown
- THEN that region's row or header is flagged (e.g. a warning chip or colored indicator)
  and the health badge at the top of the section shows at least one flag.

### INV-H-HEALTH-BADGE-OK
- GIVEN Region view is active and NO region exceeds 70% of portfolio weight
- WHEN I view the health badge
- THEN the badge reads "OK" (or equivalent) in a neutral/positive tone; no warning flags
  are shown.

### INV-H-HEALTH-BADGE-FLAGS
- GIVEN Region view is active and one or more regions exceed 70% concentration
- WHEN I view the health badge
- THEN the badge shows the count of flagged regions (e.g. "1 flag" or "2 flags") in a
  warning tone; each flagged region is individually highlighted.

---

## Configurable stress / guideline

### INV-H-STRESS-DEFAULT
- GIVEN no custom stress/guideline has been configured
- WHEN I view the Holdings sub-tab
- THEN the stress/guideline row shows a default value (e.g. "–20% drawdown scenario")
  and is labeled as the default, not as a custom setting.

### INV-H-STRESS-EDIT
- GIVEN I click the stress/guideline configure button
- WHEN the stress editor opens and I enter a custom drawdown % (e.g. –30) and click Save
- THEN the editor closes and the stress row updates to reflect the new –30% scenario; the
  label changes from "default" to reflect the custom setting.

### INV-H-STRESS-IMPACT
- GIVEN a custom stress scenario is configured
- WHEN I view the Holdings sub-tab
- THEN each holding row (or a summary row) shows the estimated impact of the scenario
  (e.g. loss in base currency if the portfolio dropped by the configured %) — not a blank
  or "–".

### INV-H-STRESS-PERSISTS
- GIVEN I configure a custom stress value
- WHEN I reload the page and return to the Holdings sub-tab
- THEN the custom stress value is still active (it persisted, not reset to default).

---

## Today's movers

### INV-H-MOVERS-ROW
- GIVEN at least one held position has a price change for today
- WHEN I view the Holdings sub-tab
- THEN a "Today's movers" row or section is visible above or within the holdings area,
  showing the top gainers and top losers for the session
- AND every chip remains on one horizontal line; when the row is wider than its pane it
  scrolls horizontally instead of wrapping.

### INV-H-MOVERS-VALUES
- GIVEN the movers row is visible
- WHEN I read a mover entry
- THEN it shows the ticker/name, today's absolute change, and today's % change — all
  consistent with the per-row "Today" column values in the holdings table.

### INV-H-MOVERS-EMPTY
- GIVEN no price data is available for today (e.g. market closed / no prices ingested)
- WHEN I view the Holdings sub-tab
- THEN the movers row either shows an appropriate "No moves today" state or is hidden
  entirely — it does NOT show stale data from a previous date labeled as "today".

---

## Responsive collapse at ≤ 1020 px

### INV-H-RESPONSIVE-COLLAPSE
- GIVEN I resize the viewport to 1020 px width (or narrower)
- WHEN I view the Holdings sub-tab in Table view
- THEN lower-priority columns (e.g. Cost basis, Weight %) are hidden; the table remains
  horizontally scrollable or shows the condensed column set without overflow breaking
  the layout.

### INV-H-RESPONSIVE-CARD-GRID
- GIVEN I am in Card view and the viewport is ≤ 1020 px
- WHEN I view the card grid
- THEN cards stack to a 2-column or 1-column layout (not a fixed 3+ column grid that clips).

### INV-H-RESPONSIVE-COCKPIT
- GIVEN the viewport is ≤ 1020 px
- WHEN I view the cockpit fold
- THEN all cockpit stat values are readable — no stat is clipped, overflowed, or hidden
  behind other content.

### INV-H-RESPONSIVE-TOOLBAR
- GIVEN Lucius or the browser narrows the Investments pane while the desktop toolbar is active
- WHEN I view the toolbar
- THEN Search, All accounts, grouping, Card view, and Table view remain on one row
- AND the flexible search/account/grouping controls shrink before the fixed Card/Table toggle;
  the toggle never drops to a second row.

### INV-H-RESPONSIVE-CARD-QUOTE
- GIVEN the Investments content pane is ≤ 640 px and Holdings uses Card view, including
  when Lucius narrows the pane inside a desktop viewport
- WHEN I view a holding card
- THEN the card uses the complete compact two-column layout: identity on the left; value,
  weight, and P&L aligned on the right; a full-width divider; and quote plus today's percentage
  in the footer, with no visible "Latest price" label consuming card space.

---

## Empty / loading / error states

### INV-H-EMPTY-NO-POSITIONS
- GIVEN no investment positions exist
- WHEN I navigate to the Holdings sub-tab
- THEN an EmptyState message is shown (e.g. "No holdings yet — add your first position")
  with no blank white area, no JS error, and no holdings table rendered.

### INV-H-EMPTY-FILTER
- GIVEN positions exist but the search filter or class filter matches nothing
- WHEN the filter is active
- THEN an EmptyState message is shown (e.g. "No holdings match your filters") with a
  clear action to reset the filters; the cockpit fold still shows portfolio totals.

### INV-H-LOADING
- GIVEN the Holdings sub-tab is loading position data from the backend
- WHEN the request is in-flight
- THEN a loading skeleton or spinner is visible — the sub-tab does not flash empty
  and then suddenly populate.

### INV-H-ERROR
- GIVEN the backend returns a 5xx error for the portfolio endpoint
- WHEN the Holdings sub-tab attempts to load
- THEN an error state is shown (e.g. "Could not load holdings — retry") with a Retry
  button; no raw error text or stack trace is exposed in the UI.

---

## Multi-position merge by instrument (custodian model)

The custodian model puts one position per instrument×account, so the SAME instrument held at
two brokers is two rows in the underlying data. When the active "Group by" does NOT itself
distinguish accounts (asset class / currency / no grouping), those rows must merge into ONE row.
Grouping by account is exempt (each group is already scoped to a single account). No venue
sublabel is ever shown on a merged row — only an account COUNT.

### INV-H-MERGE-ASSET-CLASS
- GIVEN I hold the same instrument (e.g. AAPL) at two different accounts
- WHEN "Group by" is set to "Asset class" (or "No grouping")
- THEN AAPL appears as ONE row with the SUMMED value/P&L of both accounts and a recomputed
  portfolio weight — not two separate AAPL rows

### INV-H-MERGE-CURRENCY
- GIVEN I hold the same instrument at two different accounts
- WHEN "Group by" is set to "Currency"
- THEN that instrument still appears as ONE merged row within its currency group

### INV-H-MERGE-ACCOUNT-EXEMPT
- GIVEN I hold the same instrument at two different accounts
- WHEN "Group by" is set to "Account"
- THEN the instrument appears as TWO separate rows, one under each account group — grouping by
  account is exempt from merging

### INV-H-MERGE-ACCOUNT-COLUMN
- GIVEN a merged row is showing (asset-class/currency/none grouping)
- WHEN I read its Account column
- THEN it shows an account COUNT (e.g. "2 accounts") — it never lists the individual account/venue
  names anywhere on the row (no venue sublabel)

### INV-H-MERGE-TODAY-UNCHANGED
- GIVEN a merged row is showing
- WHEN I read its Today % column
- THEN it shows the instrument's actual day-change % (identical to what either contributing
  account alone would show) — never an averaged or summed percentage

### INV-H-MERGE-NO-ACTIONS
- GIVEN a merged row is showing in Table density
- WHEN I look at its action cell
- THEN the Sell/Dividend buttons are NOT shown (the target account is ambiguous) — clicking the
  row still opens the asset detail page, whose Position tab shows the full per-account breakdown

### INV-H-MERGE-OPENS-AGGREGATE-DETAIL
- GIVEN a merged row is showing
- WHEN I click it
- THEN the asset detail page opens showing the aggregated position (see
  `docs/qa/acceptance/asset-detail-position.md` ADPOS-MULTI-*)

### INV-H-MERGE-SINGLE-ACCOUNT-UNCHANGED
- GIVEN an instrument is held at only ONE account
- WHEN any non-account grouping is active
- THEN it renders exactly as before (single row, Sell/Dividend actions present, opens the
  single-position detail) — the merge logic is a no-op for the common case
