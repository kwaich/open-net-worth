# ADR — Plan tab redesign: faithful port of the design handoff

- **Status**: Accepted
- **Date**: 2026-07-01
- **Agent provenance**: Decided by the orchestrator driving the Plan-tab redesign from
  `frontend/design_handoffs/plan_handoff/`, AFK-authorized by the owner ("re-implement the Plan tab
  to match the design exactly, E2E; if backend doesn't support, implement to support; document
  features we support now that the design omits; no deviation from the design").

## Context

The handoff ships a complete, interactive **prototype** (its own React/babel app + the production
plan stylesheet `plan.css`, 1608 lines, built on the real design tokens) plus prose specs (01
Foundation, 02 Budget, 03 Cash Flow, 04 Outlook, 05 Components). The current app already has a Plan
tab (`Planning.tsx` with `budget | projections | retirement` sub-tabs) **and** a separate top-level
`cashflow` tab. Surprisingly, the backend is already ~95% complete: a full YNAB envelope engine
(`services/planning.py`, `/api/planning/*`), deterministic FIRE (`services/fire.py`, `/api/fire`),
stochastic Monte-Carlo (`services/montecarlo.py`, `/api/montecarlo`), and a cashflow runway
projection (`services/cashflow.py`, `/api/cashflow`). The owner's hard requirement: the shipped
look + behavior **exactly** match the design; do not "compromise" by claiming the current app covers
it.

## Decisions

1. **Faithful port, not re-derive.** The prototype is the reference implementation. Translate its
   JSX → app React/TS, bring `plan.css` across verbatim (re-registered under the CSS-class
   discipline), keep DOM/class structure identical so the result is pixel-identical. Only the
   standalone-shell scaffolding (`.onw-*`, `.pl-content`, `html/body`) is stripped and the
   `.ui-page-header` overrides scoped under `.pl-page`. This is the only approach that guarantees
   zero design drift.
2. **IA merge.** The new Plan tab is **3 sub-tabs — Budget · Cash Flow · Outlook** (per the design).
   "Outlook" merges the old Projections + Retirement into one life-chart + one set of levers reading
   off one engine; the standalone `cashflow` top-level tab folds in as the Cash Flow sub-tab. New
   self-contained feature dir `frontend/src/components/tabs/plan/` (mirrors `tabs/accounts/`,
   `tabs/investments/`). The `/plan` route (`transactions` tab id) swaps from `Planning` to the new
   `Plan`; tab id/label kept to avoid nav churn. `transactions` added to `SELF_HEADER_TABS` so the
   ported PageHeader masthead owns the title. Legacy `Planning.tsx` + `planning/` + the standalone
   `CashFlow.tsx` tab stay until the verify gate passes, then are dispositioned (cleanup).
3. **Client-side engine, real-data seam.** The prototype's `PlanData` engine (`simulateLife`,
   `simulateOutcomes`, `survivalCurve`, `statusOf`, `suggestSWR`, `buildBands`, `makeTrajectory`) is
   ported **client-side verbatim** as `planData.ts`. The handoff sanctions this ("the client engine
   is a faithful stand-in"); it is also REQUIRED for fidelity — the design's fan needs
   p10/p25/p50/p75/p90 + the nest-egg distribution + the Rich/Broke/Dead bands + the scan-based
   guidance (`earliestStopAge`/`extraSavingFor`/`typicalTrim`), none of which the server
   `/api/montecarlo` (p10/p50/p90 only) exposes. Its **inputs are seeded from real endpoints** (net
   worth, FIRE profile) — the seeded constants are replaced by live data.
4. **Reuse the existing backend; one new endpoint.** Budget wires to `/api/planning/*`
   (month/assign/move/targets/groups/auto-assign-preview) + `/api/categories`; status is re-derived
   client-side via `statusOf` so `overspent`/`overfunded` match the prototype. Cash Flow wires to
   `/api/cashflow` (server projection + what-if params). Outlook seeds from `/api/fire` +
   `/api/profile`. The **only** real gap built: `GET /api/planning/income` — a fused income view
   (earned + investment income, grouped, received-this-month + YTD + taxable), composed from existing
   services, no double-count (reads `kind=income` txns, the same source as `inflows_to_assign`).
5. **Consume, don't rebuild Lucius.** The handoff freezes Lucius ("consume the assistant, don't build
   it"). The prototype's bespoke `LuciusSide` docked panel + `TweaksPanel` + `AppNav` are NOT ported;
   the existing `RouterShell` + `LuciusDock` host the tab, and every "Ask Lucius" affordance routes to
   the `onAskLucius` prefill path (the bottom-right dock pill in the design screenshots IS the real
   `LuciusDock`).
6. **FIRE Settings → profile.** The modal persists to `PUT /api/profile`
   (`fire_annual_expenses`, `fire_withdrawal_rate_pct`, `fire_expected_return_pct`,
   `fire_volatility_pct`, `retirement_target_age`, `fire_life_expectancy`; `monthly_net_income` for
   monthly saving); the server recomputes `/api/fire`. A `fireVersion` bump re-fetches Outlook so
   both sub-tabs reconcile (the prototype's "one engine, both tabs" invariant).
7. **Scenarios / life events / baseline override / income streams → `localStorage`**, exactly as the
   prototype implements them (keys `plOutlookScenarios.v1`, `plFireBaseline.v1`; Cash Flow what-if
   under `onwCashflowView`). This IS the design's behavior; server persistence is a documented future.
8. **Privacy masking.** Keep the prototype's `.mask` class (plan.css defines `body.hide-amounts
   .mask`); the app's existing privacy toggle sets `body.hide-amounts`, so it activates automatically
   (no migration to the app's `.money` class needed for fidelity).

## Intentional deviations (all handoff-sanctioned or demo-harness)

- **Tweaks panel** (privacy/accent/cashflow-shape/lx-mode), **`LuciusSide`**, **`AppNav`** — prototype
  demo harness / frozen shared services; not ported (Decision 5).
- **Cash Flow trajectory-shape what-if** (Steady climb / Seasonal swings / …) — a mock-data flavor
  swing meaningless on real `/api/cashflow` data; dropped.
- **Outlook `STAGE_OVERRIDES`** (demo personas) — driven by a removed Tweak; the real seeded FIRE
  config replaces them.
- **Budget month stepper** — the prototype hardcodes Apr–Jul 2026; the real tab uses
  `/api/planning/month?ym=` with the server `nav` (prev/earliest/can_go_back).

## Consequences

- The page-05 "promote the bespoke pickers (anchored popover, color picker, Lever, inline edit) to DS
  primitives" ask is **not** done as separate DS components in this pass — they are ported in-place to
  guarantee the exact match. Recorded as a follow-up in `PARITY_AND_GAPS.md`.
- Legacy `Planning.tsx`, `planning/`, and the standalone `CashFlow.tsx` tab are reconciled against the
  design and dispositioned in `PARITY_AND_GAPS.md` (kept-and-flagged vs removed).
- The planning/FIRE/Monte-Carlo/cashflow services and the budget choke point (`apply_budget_change`)
  are unchanged — the redesign is presentation + one additive read endpoint.

## Addendum (2026-07-01) — post-audit "no-mock" hardening pass

After the faithful port, the owner pressed a hard, repeated directive: **"DON'T SHOW ANY MOCK DATA,
MOCK FEATURE, MOCK TEXT — wire everything to the backend; change or add APIs as needed."** A
read-only audit of every Plan sub-tab classified each value/writer as wired / persona-leak /
fabricated / local-only. The findings were fixed (each verified against an isolated QA server, never
live data):

- **Category swatch colour now persists** — new `POST /api/categories/{key}/color` (custom-entry
  update for custom keys; `profile.category_color_overrides` for built-ins, applied over the shipped
  default in both `get_all_categories` twins). Supersedes the local-only `recolorCat` TODO.
- **FIRE persona-leak killed (was the worst offence).** Decision 3 above said seeded constants are
  "replaced by live data" — but `useOutlookFire` skipped the fetch at `net_worth ≤ 0` and `useState`'s
  once-only lever seed froze the demo persona (38/55/72k/1.8M) for a fresh user. Fix: always fetch
  `/api/fire` (a $0 net worth is valid; the backend resolves from the profile), expose `loading`,
  re-seed the Outlook levers from the real config on load, and defer the first sim until it resolves.
  The FIRE Settings modal likewise seeds only real/derivable profile data — unset fields stay blank,
  never persona constants. The dormant CPF-persona `streams` default was neutralised to `[]`.
- **No fabricated numbers in destructive flows.** Budget delete-confirmation modals showed an
  `activity/55` (and income `ytd/800`) *estimate* of "All transactions N"; now they show the REAL
  count via `/api/transactions?category=…&limit=1`'s `total` ("…" while loading).
- **Snooze-target toggle persists** `snooze_until` (non-destructive `PUT /api/planning/targets/{cat}`),
  reflecting the server's `status:"snoozed"`.
- **Cash Flow asset-scope previews are real.** `_market_balance_by_class` now reports EVERY class's
  market value (so a class shows its real S$ before it is toggled on, not `+S$0`); the simulated
  starting balance still adds only the user-included subset.
- **Income InflowManager wired to the backend** — income sources are `kind="income"` categories, so
  add/rename/recolor/delete/move/taxable route through the existing `/api/categories` +
  `/api/planning/groups` writers (two small additive backend gaps closed: a kind-agnostic
  `_op_move_category`, and a `POST /api/categories/{key}/taxable` over the existing
  `set_category_taxable_default`). Replaces the documented "structural edits are local-only" stub.
- **Txn deep-link.** Tapping a transaction in the category-activity popover opens the Accounts detail
  drawer (cross-tab `cashOpenTxn` signal, mirroring `cashFilter`).
- **Server persistence for scenarios / FIRE baseline** landed via `/api/plan/*` (`plan_state` store),
  superseding Decision 7's `localStorage`-only stance for those two (Cash Flow what-if stays client-side).
- **CSS-class discipline restored** — the port left ~180 dead classes in `plan.css` and orphaned ~51
  old-Plan classes in `index.css`; both removed, returning the dead-class ratchet to its floor (49).

Net new/changed backend surface from this pass: `POST /api/categories/{key}/color`,
`POST /api/categories/{key}/taxable`, a kind-agnostic `_op_move_category`, and an all-classes
`_market_balance_by_class` — all additive, all layering-clean, all unit + TestClient tested.
