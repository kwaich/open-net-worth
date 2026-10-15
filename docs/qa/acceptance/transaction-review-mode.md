# Acceptance — Transaction review mode

Verify on the running Accounts tab with synthetic automation-created transactions carrying `needs_review=true`.
Exercise both a 390×844 phone viewport and a 1440×900 desktop viewport with Lucius closed and
open. Review mode is a compact card flow, not the transaction detail drawer.

### TR-OPEN — review count opens the focused queue
- GIVEN at least two automation-created transactions need review across different accounts
- WHEN I activate “2 to review” from either the account header or All accounts overview
- THEN a “Review transactions” surface opens on the first newly-added transaction
- AND the register does not change to a “Needs review” filter

### TR-LUCIUS-ENTRY — the landing page groups the backlog as one job
- GIVEN seven automation-created transactions need review
- WHEN I open a new Lucius conversation
- THEN “Needs attention” shows a badge of 1, not 7
- AND its single row says “7 transactions added by automations”
- AND activating Review opens the same compact review surface without sending a Lucius message
- AND on mobile the transaction row remains available while Active and Scheduled stay hidden

### TR-AUTOMATION-SCOPE — origin and review state are both required
- Include scheduled transaction posts and AI-driven automation creates still needing review.
- Exclude manual, interactive Lucius, import, agent and feed rows created outside automation.
- Exclude approved and deleted rows, and manual rows merely edited by an automation.
- Apply scope before count, limit and offset; all entry points use the same scoped count.
- Keep the broader Needs review register filter unchanged.
- New single and bulk automation creates remain discoverable after audit retention pruning.

2026-09-19 evidence: `tests/test_recently_added_transactions.py` covers mixed-origin pagination,
approval removal and audit retention. Frontend queue, Accounts entry and Lucius activity tests
cover the scoped URLs and wording. Synthetic browser checks cover 375×667, 390×844 and 1440×900.

### TR-HIERARCHY — the important facts come first
- GIVEN Review mode is showing a transaction
- WHEN the card renders
- THEN account and provenance, signed native amount, date, payee, and category are visible without
  opening another surface
- AND the default card is read-only, fits without scrolling, and shows unequal transfer legs even
  when both accounts use the same currency
- AND the activity history, receipts, source-email body, and other transaction-drawer sections are
  absent
- AND Edit opens the transaction drawer already in edit mode; the card itself only changes the category

### TR-EDIT-APPROVE — edits and approval land together
- GIVEN the current card needs review
- WHEN I choose a category on the card and activate Approve
- THEN the category persists through the ledger together with the review decision
- AND any other field is changed through Edit, which opens the transaction drawer in edit mode and
  re-reads the queue after a save
- AND approving without edits changes only review state and cannot overwrite a newer transaction edit
- AND that transaction leaves the review queue
- AND the next pending card appears with the remaining count reduced by one

### TR-APPROVE-FAIL — a failed save is recoverable
- GIVEN the current card contains edits
- WHEN Approve fails
- THEN the same card and draft remain visible
- AND a specific inline error appears with a Retry action
- AND no later transaction is shown as though the approval succeeded

### TR-SKIP — skip leaves the transaction pending
- GIVEN more than one transaction needs review
- WHEN I activate Skip for now
- THEN the next card appears without a write
- AND reopening Review mode still includes the skipped transaction

### TR-REJECT — rejection is explicit and confirmed
- GIVEN the current card needs review
- WHEN I activate Reject
- THEN a confirmation names the transaction before anything is deleted
- AND cancelling keeps the same card
- AND confirming removes the transaction and advances the queue

### TR-SWIPE — card gestures have safe semantics
- GIVEN I begin a horizontal gesture on non-interactive card space
- WHEN I swipe right beyond the threshold
- THEN the same edited payload as the Approve button is submitted
- AND WHEN I swipe left beyond the threshold
- THEN the card is skipped without a write
- AND a sub-threshold gesture returns the card to rest without an action
- AND a gesture beginning on an input, select, button, or link never moves or submits the card

### TR-KEYBOARD — desktop has complete non-touch parity
- GIVEN focus is outside an editable control in Review mode
- WHEN I press ArrowRight or ArrowLeft
- THEN the card approves or skips respectively
- AND every action is also reachable by Tab and has a visible focus state
- AND arrow keys inside form fields retain their normal editing behavior

### TR-LUCIUS — Lucius updates the card in place
- GIVEN Review mode is showing a transaction
- WHEN I activate “Ask Lucius”
- THEN an inline prompt sends that exact transaction as a structured reference to the shared
  Lucius conversation
- AND Review mode remains mounted rather than navigating away
- AND after Lucius updates it, the visible card refreshes with the authoritative values
- AND if Lucius approves or deletes it, Review mode advances to the next pending row

### TR-STATES — queue lifecycle is honest
- GIVEN the queue request is pending THEN Review mode shows a labelled loading state
- GIVEN the request fails THEN it shows the failure and Retry reissues the request
- GIVEN no transactions need review THEN it shows “You’re all caught up” and Done closes the surface
- GIVEN more rows exist than the bounded fetch returned THEN the surface says more remain rather
  than claiming the entire backlog is complete

### TR-RESPONSIVE — mobile and desktop preserve the same task
- GIVEN a 390×844 phone viewport THEN Review mode is a bounded bottom sheet, respects safe-area
  insets, and keeps the complete card and action row visible without scrolling in view mode
- GIVEN a 1440×900 desktop viewport THEN Review mode is a centered dialog
- GIVEN the desktop Lucius panel opens THEN the dialog reflows without overlap or horizontal scroll
- GIVEN reduced motion is enabled THEN swipe results occur without animated card travel
