# Acceptance criteria — Accounts (Cash) tab

Interaction-level GIVEN/WHEN/THEN for the **Accounts** tab (account list,
transaction register, add/edit form, split transactions, date/tag/search
filters, reconcile).
The QA agent must exercise every criterion and report PASS/FAIL per ID.

### ACCT-LOAD
- GIVEN at least one account exists
- WHEN I click "Accounts" in the sidebar
- THEN the account list sidebar is shown (grouped by Assets / Liabilities /
  Receivables) and the transaction register shows all transactions — no JS
  error, no blank screen.

### ACCT-SELECT
- GIVEN there are multiple accounts
- WHEN I click on a specific account row in the sidebar
- THEN the register switches to show only transactions touching that account,
  and the account name appears as the register heading.

### ACCT-ADD-OPEN
- GIVEN I am on the Accounts tab
- WHEN I click the "+ Add account" button
- THEN the "Add account" modal opens with name, type, kind, currency, and
  initial-balance fields visible.

### ACCT-ADD-SAVE
- GIVEN the "Add account" modal is open
- WHEN I fill in a name ("Test Savings") and click "Create account"
- THEN the modal closes AND the new account appears in the sidebar with the
  correct name and zero balance.

### ACCT-ADD-TXN
- GIVEN I am viewing any account or All accounts
- WHEN I click "+ Add transaction"
- THEN the TransactionForm modal opens with kind, date, description, account,
  amount, and category fields.

### ACCT-ADD-TXN-MOBILE
- GIVEN I am on the mobile breakpoint (≤639.98px), viewing a specific account
- WHEN I tap "Add transaction"
- THEN the inline add-row form does NOT open; instead the app navigates to
  the Lucius tab with the composer prefilled "Add a transaction to {account
  name}: " and focused, ready to send.

### ACCT-ADD-TXN-MOBILE-NOACCT
- GIVEN I am on the mobile breakpoint, viewing All accounts (no account
  selected)
- WHEN I tap "Add transaction"
- THEN the app navigates to the Lucius tab with the composer prefilled "Add a
  transaction: " (no account clause) and focused.

### ACCT-TXN-SAVE
- GIVEN the TransactionForm is open in add mode with kind=expense
- WHEN I fill in date, description, from-account, amount, and category, then
  click Save
- THEN the modal closes AND the new transaction appears at the top of the
  register AND the account balance updates to reflect it.

### ACCT-TXN-SPLIT
- GIVEN the TransactionForm is open for an expense
- WHEN I click "Split" and add two split lines that together sum to the
  total amount, then save
- THEN the register shows the transaction with a "Split · 2 ▾" chip in the
  Category column; clicking the chip expands the two per-category lines below
  the row.

### ACCT-FILTER-DATE
- GIVEN the All-accounts register is showing transactions
- WHEN I click "This month" in the date-preset bar
- THEN the register refreshes to show only transactions dated in the current
  calendar month.

### ACCT-FILTER-SEARCH
- GIVEN I am in the All-accounts register
- WHEN I type text into the search field (the "q" filter)
- THEN after a short debounce the register refreshes to show only
  transactions whose description matches that text.

### ACCT-BULK-CATEGORISE
- GIVEN at least two transactions are visible in the register
- WHEN I check two rows via their checkboxes and then select a category from
  the "Set category…" dropdown in the bulk-action bar
- THEN both rows' category chips update immediately to the chosen category.

### ACCT-BULK-DELETE
- GIVEN at least one transaction is checked
- WHEN I click "Delete" in the bulk-action bar and confirm the dialog
- THEN the selected transactions disappear from the register AND the count in
  the dialog matches the number deleted.

### ACCT-CLEARED-TOGGLE
- GIVEN a transaction row is visible with cleared status "○" (uncleared)
- WHEN I click the "C" cell on that row
- THEN the cleared indicator changes to "✓" (cleared) and the account's
  cleared balance updates — the working balance does NOT change.

### ACCT-RECONCILE
- GIVEN I am viewing a single account
- WHEN I click "Reconcile" and enter the real statement balance, then click
  "Confirm & lock" (or "Confirm — book adjustment & lock")
- THEN the modal closes AND the account's cleared balance now equals the
  entered statement balance (an adjustment transaction is visible in the
  register if the values differed).

### ACCT-FOREIGN-CCY-TOOLTIP
- GIVEN there is a transaction in a non-base currency (e.g. EUR when base is
  USD)
- WHEN I hover over the amount cell on that transaction row
- THEN a tooltip appears showing the native amount, the base equivalent, and
  the implied exchange rate (e.g. "€1,000 ≈ $1,090 · 1 EUR ≈ $1.09").

## Card statements & reminders (CARDS-*)

### CARDS-WIDGET-RENDER
- GIVEN a credit card account exists and has statement_close_day and
  payment_due_day configured
- WHEN I click that account in the sidebar
- THEN the "Statement" panel is visible in the account detail pane showing
  two stat cards: "Current cycle (since …)" with the charge total for the
  open cycle, and "Last statement (to …)" with the charge total for the
  most-recently closed cycle; the panel also shows a payment status badge.

### CARDS-CONFIG-FIELDS-CREDIT-ONLY
- GIVEN I open the edit modal for an account
- WHEN the account type is "credit_card"
- THEN three additional NumberFields are visible: "Statement close day"
  (1–31, placeholder "e.g. 25"), "Payment due day" (1–31, placeholder
  "e.g. 15"), and "Reminder days before due" (placeholder "e.g. 3");
  these fields are NOT visible when the account type is not "credit_card".

### CARDS-STATEMENT-MATH
- GIVEN a credit card with statement_close_day=25 and expense transactions
  posted both before and after the last close date
- WHEN the Statement widget loads
- THEN "Current cycle charges" equals the sum of expense legs where the card
  is the from_account_id, dated from the day after the last close date up to
  today inclusive; "Last statement balance" equals the sum of expense legs in
  the cycle that ended on the last close date; the two figures do not overlap
  and transfer legs (payments) are excluded from both totals.

### CARDS-PAID-BADGE
- GIVEN a credit card with a non-zero last statement balance AND a transfer
  to the card (kind=transfer, to_account_id == card) was recorded on or
  after the last close date for an amount equal to or greater than the last
  statement balance
- WHEN the Statement widget loads
- THEN the payment status badge reads "PAID" with a positive (green) tone
  and no overdue or due-date countdown is displayed.

### CARDS-PARTIAL-BADGE-AND-REMINDER
- GIVEN a credit card with a last statement balance of 1000 AND a transfer
  to the card for 400 (a partial payment) was recorded on or after the last
  close date
- WHEN the Statement widget loads
- THEN the payment status badge reads "PARTIAL — 600 [CURRENCY] left" with
  a warn tone showing the remaining balance; AND when the monthly
  notify_if_unpaid Automation fires for this card it sends a notification
  referencing the remaining 600 amount — a partial payment does NOT suppress
  the reminder (only a full payment that equals or exceeds the last statement
  balance suppresses it).

### CARDS-OVERDUE
- GIVEN a credit card with days_until_due < 0 (the due date has passed this
  month) and no payment recorded against the last statement
- WHEN the Statement widget loads
- THEN the payment status badge reads "OVERDUE" with a negative (red) tone.

### CARDS-DUE-TODAY
- GIVEN a credit card whose next payment due date equals today's date and no
  payment has been recorded against the last statement
- WHEN the Statement widget loads
- THEN the payment status badge reads "DUE TODAY" with a warn tone.

### CARDS-NO-DUE-DAY-SUPPRESSED
- GIVEN a credit card with statement_close_day configured but no
  payment_due_day and no payment_due_date set on the account
- WHEN the Statement widget loads
- THEN the panel renders without a due-date label or days-until-due
  countdown (next_due_date is null in the API response); an unpaid balance
  shows the "UNPAID" badge with a negative tone rather than "OVERDUE" or
  a day-count label.

### CARDS-SAVE-DOES-NOT-CREATE-AUTOMATION
- GIVEN a credit card account is saved with statement-close and payment-due days
- WHEN the save completes
- THEN those cycle dates are persisted and no reminder Automation is created or changed.
- AND reminders are created explicitly through Automations or the Lucius "Remind me" action.

### CARDS-NON-CC-NO-WIDGET
- GIVEN an account whose type is NOT "credit_card" (e.g. checking, savings,
  or investment)
- WHEN I click that account in the sidebar
- THEN the "Statement" panel is not rendered anywhere in the account detail
  pane.

### CARDS-UNCONFIGURED-CTA
- GIVEN a credit card account exists but has no statement_close_day set
  (GET /api/accounts/{id}/statement returns HTTP 204)
- WHEN I click that account in the sidebar
- THEN the Statement panel shows the message "Add a statement close day and
  payment due day to track your billing cycle." with a "Set up now" link
  that opens the account edit modal when clicked.
