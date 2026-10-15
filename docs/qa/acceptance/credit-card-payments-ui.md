# Acceptance — Credit Card Payments rendering (Plan › Budget)

The frontend half of the YNAB-engine credit-card payment-envelope feature (ADR
`docs/adr/2026-07-09-ynab-engine-credit-card-payment-envelopes.md`). The backend already emits, on
each payment-envelope budget row, `is_payment_envelope: true`, `available_for_payment` (raw, signed,
unclamped) and `underfunded` (`max(0, card owed − available_for_payment)`), and marks the pinned
**Credit Card Payments** group with `synced: true` (same flag Commitments uses). Payment envelopes
are the only credit-card engine (the `credit_card_mode` two-mode flag was removed 2026-07-10), so
this surface is always present once a book has an on-budget credit card.

**QA runs against a QA clone — NEVER live data.** Make a `make qa-data` clone; the backend's boot
sync (`sync_card_payment_envelopes`) mints a payment envelope per on-budget credit card
automatically. Boot the QA server (`ONW_QA_MODE=1`, port 8090–8099, isolated
`DATA_DIR`/`CACHE_DIR`/`LOGS_DIR`). Groups 1–2 are the running-app UI gate; Group 3 is unit-level.

## Group 1 — The Credit Card Payments group renders (payment_envelope mode)

- **CCP-UI-1 — group present + pinned.** GIVEN a clone in `payment_envelope` mode with ≥1 on-budget
  credit card, WHEN you open Plan › Budget, THEN a **Credit Card Payments** group is shown **pinned
  above** the ordinary spending groups, carrying a **Synced** pill and a row per on-budget card.
- **CCP-UI-2 — locked like Commitments.** WHEN you inspect a card's payment row, THEN it is
  undraggable, has no rename/kebab affordances, and shows no "No categories linked" hint (it is a
  bot-managed zero-category envelope) — identical locked treatment to a Commitments row.
- **CCP-UI-3 — the column self-labels "Payment".** WHEN you look at a payment row's rightmost cell,
  THEN a small **Payment** caption sits above the figure (the table-wide header still reads
  "Available" for every ordinary envelope — unchanged).
- **CCP-UI-4 — funded state.** GIVEN a card whose `available_for_payment ≥ 0` and `underfunded ≈ 0`,
  THEN the row's status line reads **Funded** (green) and the Payment figure renders in the
  non-negative tone.
- **CCP-UI-5 — underfunded state.** GIVEN a card with `underfunded > 0` (owes more than is set
  aside), THEN the status line reads **"{amount} more needed for this bill"** (amber/warn) and the
  amount equals the row's `underfunded`.
- **CCP-UI-6 — overpaid state (negative Payment).** GIVEN a card with `available_for_payment < 0`
  (paid the card with more than budgeted), THEN the Payment figure renders **red/negative** and the
  status line reads **"{amount} sent above what you'd set aside"** — mirroring an overspent envelope.
- **CCP-UI-6b — an overpaid card counts as Overspent.** GIVEN the same overpaid card, WHEN you
  read the Summary header, THEN the **Overspent** stat counts that row and adds its
  `|available_for_payment|` to the amount beside it (and the Overspent filter chip shows the row) —
  a cash overspend is a cash overspend, which is what YNAB badges too. The UNDERFUNDED (CCP-UI-5)
  state is NOT counted there: that is card debt, and it has its own **Underfunded** figure in the
  desktop rail's Auto-assign panel. The Auto-assign menu's own "Top up overspent envelopes" hint
  still counts ordinary envelopes only — it never writes into a locked card row, and its hint may
  not promise what it will not touch.
- **CCP-UI-7 — Synced pill navigates.** WHEN you click the group's **Synced** pill (or a locked
  card row), THEN the app navigates to **Accounts** (`/accounts`).
- **CCP-UI-8 — group subtotal is the Payment sum.** WHEN the group is collapsed/summarized, THEN
  the rightmost subtotal shows **Σ available_for_payment** across its card rows (not the plain
  envelope `available`).

## Group 2 — Signed mode is byte-identical (the safety guarantee)

- **CCP-UI-9 — nothing new in signed mode.** GIVEN a clone in the DEFAULT `signed` mode (no payment
  envelopes exist), WHEN you open Plan › Budget, THEN there is **no** Credit Card Payments group and
  the budget table is exactly as it was before this change — no new rows, labels, or columns.
- **CCP-UI-10 — grand Total + RTA unchanged.** WHEN you compare the Total row and Ready-to-Assign in
  signed mode before/after this change, THEN they are identical (the new fields are a per-row display
  substitution only — they never back the table-wide totals or the RTA identity).

## Group 3 — Unit coverage (vitest, already green — run to confirm)

`budget.cardPayments.test.tsx` asserts:
- (a) signed-mode render with zero `is_payment_envelope` rows is byte-identical (no CC group mounts);
- (b) the three states (funded / underfunded / overpaid) render the exact copy + tone from Group 1;
- (c) a locked payment row's click routes through `onOpenCardPayment` (→ `/accounts`), not
  `onOpenCommitment`;
- (d) the `pl-gcount--empty` "No categories linked" hint never appears on a locked row.

```
cd frontend && npx vitest run src/components/tabs/plan/budget.cardPayments.test.tsx
```

## UI Quality Gate checklist (must all pass to call this done)

- [ ] `make qa-e2e` green for CCP-UI-1..10 (browser-use, against the flipped QA clone)
- [ ] `make ui-gate` clean (geometry)
- [ ] Screenshot evidence: the group in funded / underfunded / overpaid states + the signed-mode
      unchanged table
- [ ] Empty/loading/error states verified (group-absent = signed mode; loading/error inherit the
      table's existing states)
- [ ] `npm run lint:classes` clean (every new className token defined in the same change)
