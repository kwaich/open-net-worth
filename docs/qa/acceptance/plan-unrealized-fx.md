# Acceptance — Plan tab: unrealized-FX note in the Ready-to-Assign breakdown

**Feature:** Foreign-currency historical-rate lock, Part B (ADR 2026-07-09).
**Surface:** Plan tab → Ready-to-Assign hero → breakdown popover (`RtaFormula`, the "ⓘ" identity banner).
**Backend contract:** `GET /api/planning/month` → `unrealized_fx` (number, base ccy). RTA is unchanged.

Design intent: RTA values held foreign cash at the CURRENT rate (an FX gain on held VND/EUR is
immediately assignable — owner decision). This note makes visible *how much* of Ready-to-Assign is
unrealized currency drift, so RTA moving with the market is never a mystery. It is read-only and
never changes the RTA number.

---

### AC-1 — Gain shows on the on-budget-cash line (foreign cash appreciated)
- **GIVEN** the book holds on-budget foreign cash (e.g. VND) whose base-currency value has risen
  since the money arrived (`unrealized_fx > 0`)
- **WHEN** I open the Ready-to-Assign breakdown popover (click the RTA hero figure)
- **THEN** the identity line reads `<cash> on-budget cash (incl. +<amount> unrealized FX) − <reserved> reserved in envelopes … = <rta> Ready to Assign`
- **AND** the `(incl. +<amount> unrealized FX)` clause is muted (`.muted`) and does not alter any figure in the identity.

### AC-2 — Loss shows with a minus sign (foreign cash depreciated)
- **GIVEN** `unrealized_fx < 0`
- **WHEN** I open the breakdown popover
- **THEN** the clause reads `(incl. −<amount> unrealized FX)` (a real minus, positive amount).

### AC-3 — Hidden when there is no FX drift
- **GIVEN** a base-currency-only book, or a month with no held foreign cash, so `unrealized_fx` is 0
  (`|unrealized_fx| ≤ 0.005`)
- **WHEN** I open the breakdown popover
- **THEN** NO "unrealized FX" clause appears — the on-budget-cash line reads exactly as before.

### AC-4 — RTA number is unchanged by the note
- **GIVEN** any book
- **THEN** the Ready-to-Assign hero figure equals `on_budget_cash − reserved − assigned-in-future − baseline`
  with no contribution from `unrealized_fx` (the note is display-only; the identity math is untouched).

### AC-5 — Loading / error states unaffected
- **GIVEN** the month is still loading, or the request errored
- **THEN** the Plan tab's existing loading/error handling is unchanged (the note simply defaults to 0
  and renders nothing until data arrives — `unrealizedFx = 0` default on `RtaHero`/`RtaFormula`).

---

**Verify:** `make qa-e2e` (isolated QA ports 8090–8099, `ONW_QA_MODE=1`) exercising AC-1/AC-3 on a
seeded book with a foreign on-budget cash account; `make ui-gate` clean; screenshot of the popover
showing the note (gain) and one without it (SGD-only). `npx tsc --noEmit` + `npm run build` green.
