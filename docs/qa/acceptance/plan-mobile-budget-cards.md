# Acceptance criteria — Plan tab mobile budget cards

The card-based mobile layout for the Budget sub-tab (390px viewport width and below), replacing the horizontally-scrolling table.

**Feature**: Mobile card layout for budget envelopes — one card per envelope, grouped by group headers, no horizontal scroll.
**Commit**: `fab3b832a` (feat: card-based mobile layout for the Budget sub-tab) + `57fd77e95` (test: close mobile-card coverage gaps)
**Design**: TBD
**Component files**:
- `frontend/src/components/tabs/plan/budget.tsx` (desktop table + mobile list tree)
- `frontend/src/components/tabs/plan/budgetCards.tsx` (mobile presentational shells: MobileEnvelopeCard, MobileGroupHead, MobileNumsGrid)
- `frontend/src/components/tabs/plan/budget.mobileCards.test.tsx` (unit tests for mobile rendering)

Status is earned by evidence: every criterion clicked/driven at 390px width. "It rendered" is not a pass.

---

## Layout & scroll

### PLAN-MOBILE-CARD-NO-SCROLL
- GIVEN the Plan → Budget sub-tab at 390px viewport width (matching the owner's test device)
- WHEN the page loads and I scroll through the budget envelope list
- THEN there is NO horizontal scrollbar anywhere, NO horizontal overflow, and every card fits within the 390px width without truncation or hidden content.

### PLAN-MOBILE-CARD-STRUCTURE
- GIVEN the budget has multiple groups (e.g., Bills, Dining, Savings)
- WHEN the list renders on mobile
- THEN each group appears as:
  - A group header with the group name, envelope count ("3 envelopes"), collapse/expand chevron, and any synced status indicator ("Synced" pill for Commitments-synced groups or a "+ Add envelope" button)
  - Zero or more envelope cards beneath it (each collapsible via the header chevron)
  - A subtotal row showing the group's total assigned, total activity, and total available

### PLAN-MOBILE-CARD-ENVELOPE-CARD
- GIVEN an envelope rendered as a card on mobile
- WHEN viewing the card
- THEN it shows:
  - Envelope name (left-aligned, truncated with ellipsis if necessary)
  - Status badge ("Funded" / "Overfunded" / "Underfunded" / "Overspent" text matching desktop)
  - Target progress bar (if a target is set), matching the desktop bar's appearance
  - Three labeled figure rows: "Assigned", "Activity", "Available" (NOT swapped or mislabeled compared to desktop)
  - For a Credit Card Payment envelope: "PAYMENT" label instead of "AVAILABLE" for the third stat
  - Kebab menu (⋯) with the same options as desktop (visible/not hidden behind hover state)
  - Action button if applicable (Cover/Fund/Move button)

### PLAN-MOBILE-CARD-OUTSIDE-ENVELOPES
- GIVEN the "Outside envelopes" section (Uncategorized, Uncategorized income, Unbudgeted categories)
- WHEN the mobile view renders at end of list
- THEN each item shows as a card with consistent styling (no horizontal scroll) and all text/numbers visible without truncation.

### PLAN-MOBILE-CARD-TOTALS
- GIVEN the entire budget list on mobile
- WHEN scrolled to the bottom
- THEN a totals row/card shows:
  - "Total" label
  - Σ assigned across all envelopes
  - Σ activity across all envelopes
  - Σ available across all envelopes
  - No horizontal overflow

## Card interactions

### PLAN-MOBILE-CARD-KEBAB-MENU
- GIVEN an envelope card on mobile
- WHEN I tap the kebab menu (⋯)
- THEN a popover opens with the same actions as desktop (Rename, Merge into, Delete, Move money, etc.)
- WHEN I tap an action (e.g., "Rename")
- THEN the action proceeds as on desktop (no mobile-specific breakage)

### PLAN-MOBILE-CARD-TARGET-EDITOR
- GIVEN an envelope card with a target progress bar
- WHEN I tap/click the target bar or the card's target area
- THEN the target editor opens (or, for a locked/Commitments-synced row, the commitment/card-payment view), same as desktop — interaction is tap instead of click, but behavior unchanged.

### PLAN-MOBILE-CARD-GROUP-COLLAPSE
- GIVEN a group header with an expand/collapse chevron
- WHEN I tap the chevron
- THEN the group's envelope cards collapse/expand, and the state persists on reload (server round-trip confirmed, same as desktop).

### PLAN-MOBILE-CARD-ACTION-BUTTON
- GIVEN an envelope card with a Cover/Fund/Move action button
- WHEN I tap the button
- THEN the appropriate action sheet / modal opens (Fund, Cover, Move money), same as desktop.

## Desktop regression

### PLAN-MOBILE-CARD-DESKTOP-UNCHANGED
- GIVEN the Plan → Budget sub-tab at desktop width (e.g., 1280px)
- WHEN the page renders
- THEN the table view is identical to pre-change: four-column grid, horizontally scrollable (if needed), every row/column position and figure matches the previous version exactly.

## Edge cases

### PLAN-MOBILE-CARD-EMPTY-STATE
- GIVEN a book with zero budget envelopes
- WHEN the Budget tab renders on mobile
- THEN an appropriate empty state appears (e.g., "No envelopes — click + to create one") with no JS errors and no orphaned UI elements.

### PLAN-MOBILE-CARD-LONG-NAME
- GIVEN an envelope with a very long name (e.g., "My Emergency Fund for Unexpected Expenses")
- WHEN rendered on mobile (360px, narrowest common width)
- THEN the name truncates gracefully with ellipsis (…) and does NOT break the card layout, push text off-screen, or cause horizontal scroll.

### PLAN-MOBILE-CARD-NO-CATEGORIES
- GIVEN an envelope with zero linked categories
- WHEN the card renders on mobile
- THEN a hint or status badge appears ("No categories linked" or similar), matching desktop behavior, without layout breakage.

### PLAN-MOBILE-CARD-LOCKED-ROW
- GIVEN an envelope synced to a Commitment or a Credit Card Payment envelope
- WHEN the card renders on mobile
- THEN the row shows a locked/synced indicator (identical to desktop), and tapping the card opens the commitment/card-payment view (not the envelope target editor).

---

**Flow is "passing" when:** every criterion is PASS at 390px width against the running app, desktop view is unchanged, and any bugs are noted separately.
