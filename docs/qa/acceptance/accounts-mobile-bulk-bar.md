# Acceptance — Accounts tab bulk bar (mobile)

Mobile-specific UI for bulk actions on selected transactions. On desktop, the bulk bar shows
"Categorize" (dropdown) and "More" (floating menu); on mobile, it shows "Ask Lucius" (Lucius
handoff) and "More" (bottom sheet) to fit the narrow viewport and provide touch-friendly targets.

## A. Bulk bar — mobile "Ask Lucius" button

- **A1** GIVEN 2+ transactions selected in Accounts on a mobile viewport (≤390px width),
  WHEN I look at the bulk bar, THEN it shows "Ask Lucius" button (not "Categorize").
- **A2** GIVEN "Ask Lucius" button visible, WHEN I tap it,
  THEN the Lucius chat dock opens with one mention chip staged per selected transaction (no
  prefilled text) — same chip content (label, date, signed amount) as dragging the same
  selection onto the dock — and the bulk bar menu closes.
- **A3** GIVEN the chat dock is not open and I tap "Ask Lucius",
  THEN the dock opens (or scrolls to top if already open).

## B. Bulk bar — mobile "More" bottom sheet

- **B1** GIVEN 2+ transactions selected in Accounts on a mobile viewport,
  WHEN I tap the "More" (⋯) button, THEN a bottom sheet slides up from the bottom with:
  - A scrim (overlay) that can be tapped to close
  - A drag handle at the top
  - Title "More actions" (or the current sub-state title)
  - An X button to close
  - The action list (Categorize, Mark Cleared/Uncleared/Reviewed, Duplicate, Description…,
    Move to Account, Export, Delete) — "Categorize" only appears here on mobile; desktop keeps
    its own separate "Categorize" button next to "More"
- **B2** GIVEN the sheet is open, WHEN I tap an action (e.g. "Duplicate"),
  THEN the action fires, the sheet closes, and the selection remains active (or clears as designed).
- **B3** GIVEN the "More" button is off (menu not visible), WHEN I tap the "More" button again,
  THEN the sheet closes (toggle off).

## C. Bulk bar — bottom sheet sub-states (mobile only)

- **C0** GIVEN the "More actions" sheet is open,
  WHEN I tap "Categorize", THEN the sheet title changes to "Set category" and the list changes
  to show a "Back" button followed by the expense category list.
- **C0b** GIVEN the "Set category" sub-state, WHEN I tap a category,
  THEN the category is applied to all selected transactions, the sheet closes, and a toast
  confirms.
- **C0c** GIVEN the "Set category" sub-state, WHEN I tap "Back",
  THEN the sheet returns to "More actions" with the original action list.
- **C1** GIVEN the "More actions" sheet is open,
  WHEN I tap "Move to Account", THEN the sheet title changes to "Move to account" and the list
  changes to show a "Back" button followed by the list of accounts.
- **C2** GIVEN the "Move to account" sub-state, WHEN I tap an account,
  THEN the move action fires, the sheet closes, and the bulk bar updates (or clears).
- **C3** GIVEN the "Move to account" sub-state, WHEN I tap "Back",
  THEN the sheet returns to "More actions" with the original action list.
- **C4** GIVEN the "More actions" sheet is open,
  WHEN I tap "Description…", THEN the sheet title changes to "Set description on N" and shows
  a text input + Cancel/Apply buttons.
- **C5** GIVEN the description editor, WHEN I tap Cancel,
  THEN the sheet returns to "More actions" without applying.
- **C6** GIVEN the description editor, WHEN I type text and tap Apply,
  THEN the description is applied to all selected transactions, the sheet closes, and a toast confirms.

## D. Bulk bar — dismiss and keyboard (mobile)

- **D1** GIVEN the "More" bottom sheet is open, WHEN I press Escape or tap the scrim,
  THEN the sheet closes and the menu state clears.
- **D2** GIVEN the sheet is showing "More actions", WHEN I swipe down on the drag handle,
  THEN the sheet closes (drag-to-dismiss).

## E. Desktop regression (no mobile viewport)

- **E1** GIVEN the Accounts tab on a desktop viewport (≥920px width),
  WHEN I select 2+ transactions, THEN the bulk bar shows "Categorize" (not "Ask Lucius").
- **E2** GIVEN "Categorize" button visible on desktop,
  WHEN I tap it, THEN a floating dropdown menu appears above the bar (not a bottom sheet)
  with the category list.
- **E3** GIVEN the category dropdown is open, WHEN I click a category,
  THEN the category is applied, the menu closes, and the selection remains active.
- **E4** GIVEN the "More" (⋯) button on desktop, WHEN I click it,
  THEN a floating dropdown appears above the bar (not a bottom sheet) with the action list.
- **E5** GIVEN the "More" dropdown is open on desktop, WHEN I click an action or click outside,
  THEN the menu closes and the action fires (if clicked) or cancels (if outside).

## F. No layout overflow (mobile)

- **F1** GIVEN any bottom sheet open on a 390px mobile viewport,
  WHEN I observe the sheet, THEN no items are clipped or extend past the viewport edges;
  all buttons are at least 48px tall (minimum tap target); text is readable at --text-md or larger.
- **F2** No console errors (JS, network, DOM) when opening/closing the sheet or clicking actions.

## Acceptance criteria IDs

- ACT-MOBILE-BULK-ASK-LUCIUS-A1–A3
- ACT-MOBILE-BULK-MORE-SHEET-B1–B3
- ACT-MOBILE-BULK-SUBSTATE-C1–C6
- ACT-MOBILE-BULK-DISMISS-D1–D2
- ACT-DESKTOP-REGRESS-E1–E5
- ACT-MOBILE-LAYOUT-F1–F2
