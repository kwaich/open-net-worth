# ADR 0014 — Design tokens: one TS source generated into CSS + TS

- Status: Accepted
- Date: 2026-06-20
- Relates to: RFC `.claude/rfcs/frontend-styling-pwa-ideal.md` (this is **Phase A** of that RFC); ADR 0011 (primary action color)
- Provenance: Agent build choice (autonomous), made while implementing the RFC's foundational phase.

## Context

The design tokens lived in **two hand-maintained places**: the CSS custom properties in
`frontend/src/index.css`'s `:root` block (lines 4–98) and a partial typed mirror in
`frontend/src/components/ui/tokens.ts` (a `SpaceToken` union + `spaceVar()`, space-only).
Nothing kept them in sync — text/radius/color/shadow had no typed handle, and the two
could silently diverge in meaning. A standing RFC (frontend styling/responsive/PWA)
identified this dual source as one of four entangled liabilities and proposed a single
source of truth as the foundational, zero-visual-diff first step before any larger work.

The codebase already had the *right shape* unguarded: `tokens.ts` resolved to `var(--token)`
so there was no value fork, and a vitest guard (`css-tokens.test.ts`) asserted every bare
`var()` is defined. What was missing was a **single authoring source** that mechanically
produces both artifacts, plus drift protection.

## Decisions

1. **TypeScript object is the source; CSS `:root` and the typed unions are generated.**
   `frontend/src/design/tokens.source.mjs` is the one place token values live. A ~80-line
   codegen, `frontend/scripts/build-tokens.mjs`, emits two **checked-in** artifacts:
   `src/index.tokens.generated.css` (the `:root { --* }` block) and
   `src/components/ui/tokens.generated.ts` (the `SpaceToken`/`TextToken`/`RadiusToken`/
   `ColorToken`/`ShadowToken` unions + `spaceVar`/`tokenVar` helpers). `tokens.ts` collapses
   to `export * from './tokens.generated'`, so every existing import site is unchanged.

2. **Not Style Dictionary, not vanilla-extract.** For ~56 tokens on a single web platform,
   Style Dictionary is over-machinery and vanilla-extract would force rewriting the 18k-line
   hand-authored stylesheet into TS object literals (and its Vite plugin's peer range
   excludes the repo's `vite@8`). A small bespoke codegen is the minimum that achieves a true
   single source with a clean typed API, keeping the bespoke CSS untouched. (Documented
   tipping point: revisit Style Dictionary if a second output target appears.)

3. **The generated `:root` replaces the hand-authored one; it is imported first.** The
   `:root` block was removed from `index.css` and the generated CSS is imported ahead of it
   in `main.tsx` (`import './index.tokens.generated.css'` before `./index.css`). This
   sidesteps the `@import`-ordering constraint (the Google-Fonts `@import` must stay first in
   `index.css`) without a build-time prepend. All 3,308 existing `var(--…)` references resolve
   unchanged.

4. **Drift is made structurally impossible.** The codegen is wired as an `npm prebuild` hook
   (runs before every `vite build`) and exposed as `npm run build:tokens`. A vitest guard,
   `tokens-codegen.test.ts`, asserts the checked-in generated files are **byte-identical** to
   a fresh codegen — so editing a generated file by hand, or editing the source without
   regenerating, fails CI. The generated CSS was also added to `css-tokens.test.ts`'s scanned
   files so the moved `:root` is still covered by the undefined-token guard.

5. **Zero-visual-diff was proven, not assumed.** Before wiring, a value-equality check
   confirmed the generated `:root` is identical to the original — **56/56 tokens, same names,
   same values, no additions or omissions**. The only behavioral change in the diff is a
   pre-existing bug fixed in passing: `--bg-3` (used by `.intel-wlc-row-edit:hover`, never
   defined anywhere — a latent transparent-hover) was given a graceful fallback
   `var(--bg-3, var(--surface2))`, the guard's own prescribed remedy.

## Consequences

- One place to change a token value; the typed unions and the CSS vars can no longer drift,
  and the drift guard is CI-blocking.
- Token *coverage* is unchanged this phase — the type scale still has raw-px leakage and there
  is still no z-index scale. Completing the scales + a value-level enforcement lint is the
  RFC's Phase B, deliberately out of scope here to keep Phase A zero-visual-diff.
- The generated files are committed (for diff visibility + IDE imports) but are derived
  artifacts — never edit them; edit `tokens.source.mjs` and regenerate.
- A new `.mjs` source + `.d.mts` declaration is the one new convention; it imports cleanly in
  both Node (codegen) and Vitest (drift guard) with no TS loader.
- Later RFC phases (CSS `@layer` + CSS-Modules scoping, mobile-first responsive, PWA
  hardening) build on this source but are not yet implemented.
