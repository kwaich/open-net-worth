# Acceptance — Essentials-first account modal

Component: `frontend/src/components/tabs/accounts/AccountModal.tsx`

### AM-QC-1 · Create starts with four essentials

- GIVEN the Accounts tab
- WHEN I click **Add account**
- THEN one modal immediately shows **Name**, **Account type**, **Currency**, and
**Current balance** (or **Amount currently owed** for liabilities)
AND it does not show a method chooser, balance date, institution, last four,
colour, interest, linked entity, or note.

### AM-QC-2 · One submit creates the opening balance safely

- GIVEN valid essentials
- WHEN I click **Add account**
- THEN exactly one create request is sent, the CTA reads **Adding…** while pending,
and repeated clicks cannot create duplicates
AND omitted optional text fields are not sent as `null`
AND current balance is required, accepts zero, and is recorded as today's Starting Balance
AND credit and loan balances are entered as positive amounts owed and stored
with the liability sign convention.

### AM-QC-3 · Failure is visible and recoverable

- GIVEN account creation fails
- WHEN the create request returns an error
- THEN the modal stays open, the entered values remain, and an inline error is
shown beside the form so the user can retry.

### AM-QC-4 · Success reveals the detailed second phase

- GIVEN account creation succeeds
- WHEN the create response returns
- THEN the account is selected and the modal advances to **Edit account**
AND the essentials and Starting Balance summary remain visible
AND the edit modal restores the wide two-column details layout.

### AM-ADV-1 · Edit details retain the full layout

- GIVEN the detailed edit view
- WHEN the modal opens
- THEN name, account type, currency, and colour appear in the identity rail
AND institution, last four, and note appear first in the details column
AND institution and last four stack vertically with concise one-line help text
AND the Linked to section is absent when the account has no connector links
AND the custom colour picker opens inward without being clipped by the modal
AND linked entities and type-specific optional fields remain visible below them where applicable:

- Cash and Fixed deposit: interest rate (APY)
- Credit: credit limit, reminder days, statement-close day, payment-due day
- Loan: interest rate (APR), original principal, payoff date, minimum payment

### AM-ADV-3 · Edit scrollbar stays out of the form

- GIVEN the detailed edit view is tall enough to scroll
- WHEN the view is idle
- THEN its scrollbar is hidden, its track sits at the modal's right edge, and content keeps equal
left and right padding
AND the scrollbar appears while scrolling or while a field in the scroll region has keyboard focus.

### AM-ADV-2 · Derived classification is not editable

- GIVEN any account type
- WHEN I view the create or edit modal
- THEN there is no editable Account kind, band, spendability, budget-membership,
locked, or on-budget field; the selected account type derives all of them.

### AM-RESP-1 · Accessible responsive layout

- GIVEN a viewport at or under 640px
- WHEN I open the create or edit modal
- THEN fields stack without horizontal overflow, controls remain keyboard
accessible, validation is announced, and the primary action remains reachable.
