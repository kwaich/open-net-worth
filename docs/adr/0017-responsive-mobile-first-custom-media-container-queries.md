# ADR 0017 — Responsive system: mobile-first ladder via @custom-media + container queries

- Status: Accepted
- Date: 2026-06-20
- Relates to: RFC `.claude/rfcs/frontend-styling-pwa-ideal.md` (this is **Phase D**); ADR 0014 (token codegen), 0016 (cascade layers)
- Provenance: Agent build choice (autonomous), code-reviewed (verdict: concerns → all addressed) before commit.

## Context

Responsive layout was **desktop-first and ad-hoc**: ~13 distinct `@media` breakpoint values
(360/420/480/560/639/640/641/720/760/900/1020/1023/1024) across the stylesheets, despite a
`breakpoints.ts` module that declared a 640/1024 contract but was a **dead export** (the
width-reactive JS read `window.innerWidth` directly). There were **no container queries**, only
**2 `env(safe-area-inset-*)`** uses in 18k lines, sub-44px touch targets, and 5 ad-hoc
`@media (hover: none)` carve-outs. The densest view — the transaction register — was
`min-width: 880px` inside an `overflow-x: auto` scroller, forcing horizontal side-scroll on a
390px phone instead of reflowing.

## Decisions

1. **One mobile-first breakpoint ladder from a single source.** `src/design/breakpoints.source.mjs`
   (sm=640, md=768, lg=1024, xl=1280) is the sole source; `build-tokens.mjs` emits
   `index.breakpoints.generated.css` (`@custom-media --sm/--md/--lg/--xl` + `--below-*`/`--sm-only`
   aliases), and `breakpoints.ts` + a typed `useBreakpoint()` matchMedia hook serve JS. A
   `breakpoints-codegen.test.ts` drift guard asserts the generated CSS **and** the `breakpoints.ts`
   values match the source — CSS and JS cannot drift.

2. **`@custom-media` resolved at build time via `postcss-custom-media`** (a `postcss.config.cjs`,
   auto-discovered by Vite — `vite.config.ts` untouched). Note: the package is `postcss-custom-media`,
   NOT the (non-existent) `@csstools/postcss-custom-media` the spec named. `var()` is illegal inside
   `@media`, so a build step is required; this is the standard one.

3. **Container queries for component-level reflow**, each behind `@supports (container-type: inline-size)`
   **with a `@media (max-width: …)` fallback** so browsers below the container-query floor still reflow.
   The headline case — the transaction register — reflows to an explicit stacked card (amount pinned to
   its corner, payee/desc/category placed by grid-area, decoration cells hidden, `min-width: 0`) at
   `< 640px`, eliminating the phone side-scroll. Also LotsPanel, AssetDetailView, PositionSubtab, the
   budget grid, YourPositionCard, and the Investments target bars.

4. **Tokenized safe-area + touch.** `--safe-t/-r/-b/-l = env(safe-area-inset-*, 0px)` applied via
   `max(base, env())` at every fixed/edge surface (chat composer, bulk bar, sidebar, topbar) — zero raw
   `env()` remain. `--tap-min = 44px` grows hit area via **padding, not min-height** (preserves dense
   layout), gated to `@media (pointer: coarse) and (max-width: 767.98px)` so desktop/touch-laptops are
   untouched.

5. **Off-scale token additions only** (per the user's decided policy): `--space-0 = 2px`, a `--text-18`
   step, and a `--text-display-*` tier for 26–44px hero numerals. The full migration of the ~500 raw
   `px` font-sizes onto the scale is **deferred** (high-volume, low-value; `lint:tokens` keeps warning).

6. **`check-breakpoints.mjs`** — a new gate (wired into `make build` + `ui-gate`) that fails on any NEW
   raw-px `@media` width outside the ladder; the 13 legacy boundaries are allowlisted and each future
   pass shrinks the list.

## Consequences

- One breakpoint source feeds CSS and JS, drift-guarded; the dead `BP` export and the deprecated
  `mobile`/`tablet` aliases are removed.
- **Desktop render is unchanged**: breakpoint renames kept `max-width` form, container queries fire only
  when the container is narrow, safe-area falls back to 0px, tap-target is coarse+narrow only.
- **Pending visual QA:** the register card reflow and the touch/safe-area changes are unit/lint-verified
  and desktop-parity-reasoned, but the user-facing narrow-width layout still needs the **UI Quality Gate**
  (`make qa-e2e` + screenshots at ~390px) before it's "done" per CLAUDE.md.
- New devDependency `postcss-custom-media`.
- Phase D-4 (full mobile-first inversion of shell rules) and the px→token font sweep remain.
