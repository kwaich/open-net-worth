# Acceptance criteria — Plan tab redesign (Budget · Cash Flow · Outlook)

Interaction-level GIVEN/WHEN/THEN for the faithfully-ported Plan tab
(`frontend/design_handoffs/plan_handoff/`). The QA agent exercises every criterion against the
RUNNING app and reports PASS/FAIL per ID. Fidelity is the bar: the rendered result must match the
prototype + `shots/*.png`. `PLAN-STICKY`, `PLAN-BUD-TABLE-SCROLL`, `PLAN-CF-ZONES`,
`PLAN-OUT-DRAG`, and `PLAN-RESPONSIVE` catch the high-value regressions (broken sticky stack,
clipped table, wrong chart zone colors, dead drag-to-retire, broken container-queries).

## Environment notes

- Run against a QA instance on ports **8090–8099** with `ONW_QA_MODE=1`. **Never** drive `:5174`/`:8080`.
- Verify computed values via the API where possible: `GET /api/planning/month?ym=`,
  `GET /api/planning/health?ym=`, `GET /api/planning/income?ym=`, `GET /api/cashflow?...`,
  `GET /api/fire?current_nw=`, `GET /api/profile`. Don't rely on the UI alone for numbers.
- The Outlook Monte-Carlo is **client-side and seeded** — the fan/odds are deterministic for the same
  inputs; dragging a lever re-runs it live.

---

### PLAN-SHELL
- GIVEN I open `/plan`
- THEN I see a sticky masthead: title **Plan** + subtitle, a **FIRE Settings** button (right), and a
  `PageTabs` row **Budget · Cash Flow · Outlook**; no duplicate app `.tab-header` above it.

### PLAN-STICKY
- GIVEN the Budget tab with a long envelope list
- WHEN I scroll the page down
- THEN the masthead (Plan + sub-tabs) stays pinned at top, the toolbar (filter + auto-assign) pins
  directly beneath it, and the table column header pins beneath that — three stacked sticky layers,
  no overlap, no stranded header (the `--pl-mast-h`/`--pl-tools-h` offsets stay exact while chips
  wrap at narrow widths).

### PLAN-BUD-SUMMARY
- GIVEN the Budget tab for a month with overspent + underfunded envelopes
- THEN the summary card shows **Ready to Assign** (= `ready_to_assign`, colored pos/neg/zero), the
  month stepper (‹ month ›), **Overspent**, **Underfunded**, **Total available**, **Age of Money**
  (days; "not enough history" when `forecastable:false`), and a Lucius action ribbon naming the one
  recommended move. Clicking Overspent/Underfunded filters the table.

### PLAN-BUD-MONTHNAV
- GIVEN the Budget tab
- WHEN I step the month back/forward
- THEN every figure recomputes from `GET /api/planning/month?ym=`; the back arrow disables at
  `nav.earliest` (`can_go_back:false`).

### PLAN-BUD-ASSIGN
- GIVEN an envelope row
- WHEN I click its **Assigned** cell, type an amount, press Enter
- THEN it POSTs `/api/planning/assign`, Ready-to-Assign and the row status/bar recompute live, and the
  value persists across a reload.

### PLAN-BUD-ACTIONS
- GIVEN an overspent / underfunded / overfunded envelope
- WHEN I use its hover **Cover** / **Fund** / **Move** action
- THEN the correct amount moves (cover = −available, fund = needed, move = excess over target) via
  assign/move, with a toast; the chip flips to funded/green.

### PLAN-BUD-AUTOASSIGN
- GIVEN the Auto-assign menu
- THEN it lists Fund all targets / Top up overspent envelopes (each with count + amount) + Assigned/Spent
  last month + Average assigned + Reset; picking one applies it (last-month/average use
  `/api/planning/auto-assign-preview`).

### PLAN-BUD-TABLE-SCROLL
- GIVEN a narrow content column (e.g. with the Lucius panel open)
- THEN the **table** scrolls horizontally inside its own box (min-width preserved) while the page
  keeps its viewport padding; the sticky header mirrors the body's horizontal scroll (columns stay
  aligned); row popovers (kebab, color, activity) render fixed-positioned and are NOT clipped.

### PLAN-BUD-DND
- GIVEN envelopes and groups
- WHEN I drag a category within/between groups, or reorder a group, by its grip handle
- THEN the order updates and persists (`/api/planning/groups/*`); the Commitments group is locked
  (no drag), as in the design.

### PLAN-BUD-COLOR / PLAN-BUD-KEBAB
- GIVEN a category color dot / row "⋯"
- THEN the color dot opens a preset palette + full HSV+hex custom picker; the kebab (fixed-
  positioned, not clipped by the table scroll box, height-capped with its own internal scroll for
  a group-heavy book) offers, in order: **Add categories…** (only for a zero-category envelope
  with unlinked candidates, above a divider) → **Rename** / **Rename envelope** (an N>1 row) →
  **Edit target… / Add target…** → **Move to group** (a header + one entry per non-locked
  sibling group) → **Merge into…** (only if another mergeable target exists) → **Add
  categories…** (again, for a non-empty envelope with unlinked candidates left) → **Remove from
  envelope** (a header + one entry per linked category — offered even on a 1:1 row per the
  handoff's unlink-semantics flip; ABSENT for a zero-category row) → **Hide/Unhide** (absent for a
  zero-category row) → **Delete envelope…** (danger-styled, always present, every row type).
  Envelope-lifecycle handoff details (unlink/delete/restore data semantics): see
  `docs/qa/acceptance/budgeting.md` `BUD-ENV-UNLINK-1TO1` / `BUD-ENV-DELETE` / `BUD-ENV-RESTORE`.

### PLAN-BUD-TARGET
- GIVEN editing a category target
- THEN I can pick **Set aside** vs **Refill up to**, a cadence (weekly/monthly/yearly/custom), and an
  amount; the ring + breakdown preview this month's funding; Save persists via
  `PUT /api/planning/targets/{cat}` (set_aside ⇄ needs_whole_amount=true, refill ⇄ false).

### PLAN-BUD-ACTIVITY
- GIVEN a category with activity this month
- WHEN I click its **Activity** figure
- THEN a popover lists the month's REAL transactions (account · date · payee · note · amount) summing
  to the activity figure — not synthetic rows.

### PLAN-BUD-INCOME
- GIVEN the **Inflow categories** button
- THEN the income view groups inflows (Employment / Investment income / …) with this-month **received**
  + **year-to-date** + a taxable subtotal, matching `GET /api/planning/income?ym=`.

### PLAN-CF-HERO
- GIVEN the Cash Flow tab
- THEN the hero shows **Cash on hand** (= current month balance) + **Financial runway** (=
  `income_runway_months`, toned pos/warn/neg) + the in/out burn, from `GET /api/cashflow`.

### PLAN-CF-ZONES
- GIVEN the balance-trajectory chart
- THEN the line + area is split at the **safety floor** and at **zero**: blue above the floor, **amber**
  below the floor (tight), **red** below zero; the dashed floor line, the "today" divider, the low-point
  marker, and (if cash runs dry) a "runs dry" zero-crossing marker are shown; hovering reads out that
  month's in/out/net/balance.

### PLAN-CF-LEDGER
- GIVEN the ledger below the chart
- THEN months group by year (collapsible), the column header is sticky and mirrors the body's
  horizontal scroll, rows with events expand to show the scheduled breakdown, older actuals collapse
  behind "show earlier", and **Today** scrolls the current month under the header; the All/Has-events/
  Tight filter + Export CSV + Collapse-all work.

### PLAN-CF-WHATIF
- GIVEN the **What-if** modal
- WHEN I change monthly income/expenses, growth %, safety floor, asset toggles, or include-commitments
- THEN the projection + runway + chart zones re-fetch from `/api/cashflow` with those params; "What-if
  active" shows; inputs persist (localStorage) across reload.

### PLAN-OUT-HEADLINE
- GIVEN the Outlook tab
- THEN the headline shows the **% chance money lasts** (success probability), a verdict badge
  (On track / At risk / Off track), a "Heads up" SWR caveat reconciling the chosen rate vs
  `suggestSWR(horizon)`, and **Safe to retire / FIRE number / Pot at retirement** stat cards — all
  seeded from real net worth (`/api/fire`, `total`).

### PLAN-OUT-DRAG
- GIVEN the Net-worth life chart
- WHEN I drag the **Retire** pill along the retirement line
- THEN the target age changes and the fan, the odds %, the safe-to-retire marker, and the nest-egg
  p10–p90 distribution all re-run together (live, client-side).

### PLAN-OUT-RBD
- GIVEN the chart mode toggle
- WHEN I switch to **Rich · Broke · Dead**
- THEN I see per-age stacked bands (dead / broke / rich tiers) to 100%; the **Condition on survival**
  toggle drops the mortality wedge; **full-screen** expands the chart (Esc closes).

### PLAN-OUT-LEVERS
- GIVEN the levers rail
- THEN monthly saving and retirement spending are obvious money inputs with no slider or maximum;
  age and percentage assumptions retain quick sliders beside visibly editable exact-value inputs.
  Changing any value re-runs the sim; "Retire when I hit my number" derives the age. Life events,
  guaranteed-income streams, and the four market shocks each re-shape the fan.

### PLAN-OUT-MONTHLY-SPENDING
- GIVEN a base-currency FIRE plan with monthly saving and monthly retirement spending
- WHEN I enter values above the former slider ranges, save the plan, and reload
- THEN both values persist unchanged with no maximum; retirement spending is labelled per month,
  its helper shows the correct annual equivalent and FIRE number, and the projection consumes the
  monthly retirement amount exactly once.

### PLAN-OUT-COMPACT-MONEY
- GIVEN a billion-scale base-currency amount (including VND with comma number formatting)
- WHEN I view it in the Accounts sidebar and hover the Plan Outlook projection fan
- THEN Plan and Accounts use the shared `utils/format.ts` Intl compact formatter (for example,
  `2,008,437,944 VND` reads `₫2.01B` under the comma/en-US preference), preserving the configured
  locale's compact suffix and currency placement. The Outlook fan tooltip keeps the exact full
  base-currency values in title metadata for Typical, Range, and Your plan.

### PLAN-OUT-SCENARIOS
- GIVEN the Scenarios section
- THEN I can save the current levers as a named scenario, load/rename/delete it, and "Compare two
  plans (Plan B)" shows A-vs-B odds side by side (localStorage-persisted).

### PLAN-FIRE
- GIVEN the **FIRE Settings** modal (from the masthead)
- THEN it has monthly retirement spending / withdrawal rate / expected return / volatility / target age / life
  expectancy / monthly saving (each with an info tooltip), a live **FIRE number = monthly retirement spending × 12 ÷ rate**
  preview, and a **Suggested** SWR chip; Save persists to `PUT /api/profile` and BOTH Cash Flow and
  Outlook reflect it (Outlook re-fetches `/api/fire`).

### PLAN-RESPONSIVE
- GIVEN a narrow viewport (or the Lucius panel open)
- THEN container-queries adapt: the budget summary stacks (hero above a compact mini-stat row), the
  filter chip row collapses to a single dropdown, the outcome/stat tiles reflow (4-up → 2×2 →
  stacked), and charts fit their container — matching the prototype's breakpoints. No horizontal page
  scroll; only the budget table scrolls internally.
- AND at ≤640 px the PageHeader subtitle is hidden, Plan and FIRE Settings share one row, and the
  Ready-to-Assign card keeps its amount, month controls, overspent, underfunded, available, and
  insight content in a compact sticky layout.

### PLAN-PRIVACY
- GIVEN privacy mode on (the topbar toggle / `h`)
- THEN every monetary `.mask` amount across all three sub-tabs is masked; labels/percentages remain.

### PLAN-LUCIUS
- GIVEN any "Ask Lucius" affordance (summary ribbon, chart insight, headline CTAs)
- WHEN I click it
- THEN the shared Lucius dock/panel opens with the question prefilled (no bespoke in-tab chat panel).
