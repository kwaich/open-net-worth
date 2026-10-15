# Acceptance: Investments › Options sub-tab

RFC: `docs/rfc/investments/06-options.md`
Lane: `lane/ui-options`

---

## GIVEN options positions exist, WHEN I open the Options sub-tab

**THEN** I see:
- A summary bar with four cards: **Open positions**, **Premium collected**, **Capital committed**, **Nearest expiry**.
- An **Income overlays** section containing covered-call and cash-secured-put rows.
- A **Directional & hedges** section containing protective-put and long-call/put rows.
- A net-P&L footer paragraph followed by an **"✦ Ask Lucius about options"** button.

---

## GIVEN a short NVDA 1,300 call expiring in 9 days, WHEN the row renders

**THEN** the DTE chip carries both classes `opt-dte` and `soon` (amber `--warn` background), and shows `9d`.

---

## GIVEN a long AMD call that is in-the-money, WHEN the row renders

**THEN** its status badge reads **ITM** and has `tone="warn"` (class `.ui-badge--warn`).

---

## GIVEN no options positions, WHEN I open the Options sub-tab

**THEN** I see the empty state: title **"No options positions"**, body text about covered calls / hedges, and two WORKING CTAs: **Record option** (primary — opens the Record option modal) and **Ask Lucius** (ghost — pre-fills the composer). No table or section headings are rendered. (Amended by options-e2e 2026-07-12: the former dead "Add holding" / "Connect brokerage" buttons are retired.)

---

## GIVEN any state, WHEN I click "Ask Lucius about options"

**THEN** the Lucius composer opens pre-filled with a message about assignment risk and premium collected.

---

## GIVEN options exist where only one strategy group is present, WHEN the sub-tab renders

**THEN** only the matching section renders; the empty section is suppressed entirely (no blank panel).

---

## GIVEN an option position, WHEN I view net worth or the Holdings sub-tab

**THEN** the option does **not** appear in the holdings table, and its value does **not** sum into the liquid net worth total (`bucket = contingent`).

---

## GIVEN the nearest expiry is ≤ 14 days away, WHEN the Nearest expiry card renders

**THEN** the card value carries `tone="warn"` (amber colour).

---

# Write flows (options-e2e, 2026-07-12)

## GIVEN the Options sub-tab (any state), WHEN I click "Record option"

**THEN** the Record option modal opens with: a Strategy select (Covered call / Cash-secured put / Long call / Long put / Protective put / Custom — presets derive type+side; Custom exposes Type and Side segmented controls), Underlying and Account selects populated from real instruments/accounts, Strike / Expiry / Contracts / Multiplier(100) / Premium-per-share / Currency / Open date fields, an optional "Book premium cash leg to" wallet select defaulting to **"Don't book a cash leg"**, a Note field, and a live preview line `Total premium: contracts × multiplier × premium/share` (plus CSP collateral when Cash-secured put is selected).

## GIVEN a valid Record option form, WHEN I submit

**THEN** `POST /api/options` is called, the modal closes, the book refetches, and the new contract appears in the correct section (short call/put with covered_call/csp tag → Income overlays; long → Directional & hedges) with correct premium math.

## GIVEN an identical open contract already exists, WHEN I submit the same signature

**THEN** the server's double-book 400 renders inline in the modal (message mentions the existing contract) and a **Record anyway** button appears which resubmits with `allow_duplicate=true`.

## GIVEN an open contract row, WHEN I click Close and submit with a buyback price + wallet

**THEN** `PATCH /api/options/{id}` (`type=close`) books exactly one ledger row (short → expense "buy-to-close", long → income "sell-to-close", `external_id=option-close:<id>`), the contract leaves the open book, and the tab refetches.

## GIVEN an open contract row, WHEN I click Close and submit with NO buyback price

**THEN** the contract closes with a status flip only — zero new ledger rows.

## GIVEN an open contract row, WHEN I click Delete and confirm

**THEN** `DELETE /api/options/{id}` removes the mistaken entry (the dialog states no cash leg is booked) and the row disappears on refetch.

## GIVEN Lucius records an option via open_option, WHEN the turn completes

**THEN** the `data_changed {domains:[options,…]}` SSE fires and the Options sub-tab refetches without a manual reload (useDataVersion('options')).
