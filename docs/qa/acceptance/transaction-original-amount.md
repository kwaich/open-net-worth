# Acceptance criteria — Original foreign charge on transactions

Records the original foreign amount + currency on a transaction (e.g. an SGD card billed in USD),
distinct from the converted account-currency leg. Display/audit only — never changes balances or
base/spend math. Spec: `docs/superpowers/specs/2026-06-22-transaction-original-amount-design.md`.

### TXN-ORIG-FOREIGN-BLOCK-SHOWS
- GIVEN I am adding a transaction with an SGD account selected
- WHEN I change the Currency field from "SGD" to "USD"
- THEN a "Settled in SGD" field appears with the help text "Leave blank to convert USD automatically at today's rate."

### TXN-ORIG-BLOCK-HIDES-SAME-CURRENCY
- GIVEN I am adding a transaction with an SGD account and the foreign block is showing (Currency = USD)
- WHEN I change the Currency back to "SGD" (the account's currency)
- THEN the "Settled in SGD" field disappears

### TXN-ORIG-SETTLED-PROVIDED
- GIVEN I am adding an expense on an SGD card with Amount = 19, Currency = USD
- WHEN I enter "25.62" in "Settled in SGD" and save
- THEN the transaction is created with a S$25.62 leg AND its row shows a muted original line "US$19.00" beneath the amount

### TXN-ORIG-SETTLED-BLANK-AUTOCAPTURE
- GIVEN I am adding an expense on an SGD card with Amount = 19, Currency = USD
- WHEN I leave "Settled in SGD" blank and save
- THEN the transaction is created (the backend converts USD→SGD) AND its row shows a muted original line "US$19.00" beneath the converted amount

### TXN-ORIG-EDIT-ROUNDTRIP
- GIVEN a foreign expense exists (settled S$25.62, original US$19.00)
- WHEN I open it for edit
- THEN Amount shows 19, Currency shows USD, and "Settled in SGD" shows 25.62

### TXN-ORIG-SETTLEMENT-PRESERVED
- GIVEN a foreign expense exists (settled S$25.62, original US$19.00)
- WHEN I edit only the "Settled in SGD" amount to "25.80" and save
- THEN the leg updates to S$25.80 AND the original line still shows "US$19.00" (the original is unchanged)

### TXN-ORIG-TRANSFER-NO-BLOCK
- GIVEN I am adding a transfer
- WHEN I view the form (any currency)
- THEN no "Settled in …" foreign block appears (transfers use the existing To-amount/To-currency legs)

## Known limitations (v1 — deliberate, NOT tested)

- **No client-side FX preview / cold-cache fail-closed.** When "Settled in <ccy>" is left blank,
  the backend converts the foreign leg using its cached FX rate. If that cache is cold the backend
  falls back to a 1:1 passthrough (with a logged warning) — a **pre-existing** behaviour of
  `to_base` (`backend/core/fx.py:86`), not introduced here, and *mitigated* by this feature because
  the original foreign amount is still auto-captured. The spec's §7 fail-closed gate (thread an
  `fx` prop, show an auto-converted preview, block submit on a cold cache) is deferred to a future
  iteration. Workaround today: type the real posted amount into "Settled in <ccy>" to bypass FX
  entirely. Tracked as a follow-up; intentionally absent from the criteria above.
