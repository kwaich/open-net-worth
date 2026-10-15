# Acceptance criteria — Commitments tab (2026-07-22 redesign)

Interaction-level GIVEN/WHEN/THEN for the redesigned **Commitments** tab — funding-outlook
masthead, needs-your-attention lane, filterable list, 4-tab detail screen, fix-the-gap flow.
Companion: `docs/adr/2026-07-22-commitments-tab-redesign-faithful-port.md`,
`frontend/design_handoffs/commitments_handoff/PARITY_AND_GAPS.md`. The QA agent must exercise
every criterion against the RUNNING app and report PASS/FAIL per ID — supersedes the pre-
redesign IDs (COM-LOAD/COM-CARD-*/COM-TIMELINE etc.), which described the retired table-based
UI and no longer apply.

### COM-EMPTY-STATE
- GIVEN no commitments have been created
- WHEN I view the Commitments tab
- THEN the Lucius-first onboarding hero is shown ("Plan the big payments ahead"), a "Draft a
  commitment with Lucius" button, four kind tiles (Purchase / Loan repayment / Scheduled
  payments / Savings & investing), and the "Lucius manages all of this for you" helper rows —
  no masthead, no needs lane, no list.

### COM-ADD-KIND-PICKER
- GIVEN I am on a populated Commitments tab
- WHEN I click "New commitment"
- THEN a sheet opens with 4 kind options (Purchase / Loan repayment / Scheduled payments /
  Savings & investing goal); clicking one closes the sheet and opens the Lucius dock with a
  prefilled, kind-appropriate message in the composer.

### COM-MASTHEAD-VERDICT
- GIVEN at least one active, non-tracker commitment exists
- WHEN I view the Commitments tab
- THEN the funding-outlook masthead shows a verdict badge + headline ("Fully covered" in green,
  or "Short [amount]" in red/amber), a floor-relative subline, and four stats (Committed
  remaining / Due in 30 days / Set aside per mo / Cash earmarked).

### COM-MASTHEAD-CHART
- GIVEN the masthead is visible
- WHEN I hover over a month column in the cushion chart
- THEN a tooltip appears showing that month's payments-out / funding-in totals and the
  end-of-month cushion balance; hovering near the right edge flips the tooltip left instead of
  clipping off-screen.

### COM-MASTHEAD-HORIZON
- GIVEN the masthead chart is visible
- WHEN I click a different horizon option (6m/12m/24m/36m/All)
- THEN the chart re-renders for that many months and the "Funding outlook · next N months"
  eyebrow updates to match; no option is offered past the furthest unpaid installment.

### COM-RIBBON-FIX
- GIVEN the verdict is "at risk" (a shortfall exists)
- WHEN I view the Lucius ribbon under the chart
- THEN it states the at-risk commitment, the shortfall amount, and the date, plus a computed
  one-line fix hint; clicking "Fix with Lucius" opens the real Lucius dock with a prefilled
  message describing the gap (not a blank composer).

### COM-NEEDS-LANE
- GIVEN at least one installment is overdue, OR an unmarked ledger transaction matches an
  unpaid installment, OR an action is due within 21 days
- WHEN I view the Commitments tab
- THEN the "Needs your attention" lane appears directly under the masthead, listing each item
  with the right action buttons (Mark paid / Confirm paid + Not this / Mark done + Not
  happening), capped at 3 rows with a "Show N more" expander when there are more.

### COM-NEEDS-CONFIRM-PAID
- GIVEN a "looks paid" suggestion is shown in the needs lane
- WHEN I click "Confirm paid"
- THEN the linked installment is marked paid with the matched transaction linked, and the
  suggestion disappears from the lane on refresh.

### COM-LIST-FILTERS
- GIVEN multiple commitments exist across different states
- WHEN I click each filter tab (Active / At risk / Goals / Completed)
- THEN the row count updates to match, each tab shows a live count badge where applicable, and
  an empty filter shows a filter-specific empty state (not a blank list).

### COM-LIST-ROW-RISK-CHIP
- GIVEN a commitment's coverage is "at_risk"
- WHEN I view its row in the list
- THEN a red "Short [amount]" chip is shown and the row carries a red left-border accent — not
  a green "Funded" chip.

### COM-DETAIL-OPEN-AND-BACK
- GIVEN at least one commitment row is shown
- WHEN I click the row, then click "← All commitments"
- THEN the detail screen opens (header, stat strip, tabs) and clicking back restores the list
  view with the same filter/sort state.

### COM-DETAIL-TABS
- GIVEN the detail screen is open
- WHEN I click each tab (Overview / Schedule / Funding / Actions & events)
- THEN the corresponding panel renders (condensed digests / installment rail / funding bar +
  source list / actions checklist) and a tab with pending items shows its count badge.

### COM-DETAIL-MARK-PAID-PARTIAL
- GIVEN an unpaid installment in the Schedule tab
- WHEN I click "Mark paid", enter an amount LESS than the full installment, and confirm
- THEN the installment splits into a paid row (for the entered amount) and a new "— balance"
  row (for the remainder, still unpaid) — verify via a page reload that the split persisted.

### COM-DETAIL-FUNDING-WATERFALL
- GIVEN an unpaid installment with at least one funding source available before its due date
- WHEN I view its row in the Schedule tab
- THEN a "Funded by" breakdown appears listing each contributing source and amount, with an
  "Unfunded [amount]" chip if the sources don't cover it in full.

### COM-DETAIL-NOT-HAPPENING
- GIVEN a pending action tied to a funding source (shows "unlocks funding")
- WHEN I click "Not happening"
- THEN the action shows a "Not happening" badge with a plain-language impact line, the linked
  funding source is disabled (verify in the Funding tab: "removed — not happening"), the
  masthead verdict re-computes on refresh, and clicking "Plan the fix with Lucius →" opens the
  dock prefilled with the specific consequence — "Undo" reverses both the action and the source.

### COM-DETAIL-LOAN-INTEL
- GIVEN a `loan_repayment` commitment created with stored loan terms
- WHEN I view its detail screen
- THEN a "Loan balance" stat (APR + months left) replaces "Funding lined up", and the insight
  ribbon shows the early-payoff scenario (months saved + interest saved for an extra $200/mo).

### COM-DETAIL-CANCEL-DELETE
- GIVEN the detail screen's kebab menu is open
- WHEN I click "Cancel commitment" (or "Delete commitment") and confirm
- THEN cancelling dims the row and moves it to the Completed filter (history kept); deleting
  removes it entirely and returns to the list — both show a confirm strip before the button
  that actually acts.
