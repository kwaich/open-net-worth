# Dark/light themes live in the token pipeline, keyed by `data-theme`, defaulting dark

- **Date:** 2026-07-21
- **Status:** Accepted
- **Spec:** `docs/superpowers/specs/2026-07-21-dark-light-theme-design.md`
- **Agent provenance:** designed and built autonomously from the owner directive "support both
  dark and light mode … design system supported … light mode shouldn't be white but something
  more elegant and easier to read"; decisions below were made on the owner's behalf and are
  reversible at the token layer.

## Context

The app shipped one hand-tuned dark palette. Every color already rides the generated token
pipeline (`src/design/tokens.source.mjs` → `index.tokens.generated.css` /
`tokens.generated.ts`), and components, tab CSS, and SVG charts consume `var(--token)` almost
everywhere — but the codegen emitted a single `:root` block, and a few dozen literals baked in
"the surface behind me is dark."

## Decision

1. **Themes are a token concern.** A color/shadow token tuple may carry an optional third
   element — its light value: `['bg', '#0e1014', '#edeae3']`. `generateCss()` emits the dark
   `:root` block unchanged (dark stays the default: an install that never touches the setting
   renders as before) plus a `:root[data-theme='light']` block containing only forked tokens,
   with `color-scheme: dark`/`light` so native controls and scrollbars follow. Non-color
   categories never fork. Rejected: a second hand-written light stylesheet (drifts from the
   source of truth), `prefers-color-scheme`-only (no user override, no designed palette),
   runtime `setProperty` of every token (bypasses the generated-artifact drift guards).

2. **Theme preference is per-device, not instance config**: `'dark' | 'light' | 'system'` in
   `localStorage['onw-theme']`, owned by `src/theme.ts` (apply/subscribe/`useTheme`,
   `matchMedia` follow for `system`, `<meta name="theme-color">` sync). An inline pre-paint
   script in `index.html` mirrors the resolution so a reload never flashes the wrong theme.
   `'system'` resolves to a concrete `dark`/`light` in JS — the CSS carries no media query, so
   the two resolution paths can't disagree. The brand `--accent` runtime override
   (`ThemeProvider`) is orthogonal and untouched — it overrides both themes identically.

3. **Light palette is warm paper, not white** (owner requirement): ivory page `#edeae3`,
   cream cards `#f5f3ec`, near-white raised surfaces `#fbf9f4`, slate ink `#23262e`; value
   colors (good/bad/warn/…) re-tuned darker for readable contrast on light surfaces. Full
   table in the spec.

4. **Canvas charts resolve tokens at build time.** Chart.js can't read `var()`;
   `utils/chartTheme.ts::cssToken(name)` reads the computed value off `<html>`, and the three
   Chart.js components (Overview, FanChart, TrajectoryChart) depend on `useTheme().resolved`
   and rebuild on flips. SVG charts already used `var(--token)` and needed nothing.

5. **Literal sweep policy.** Dark-baked literals were rewritten onto tokens or
   `color-mix(…)` over tokens (`white`-lift hovers → `--ink`-mix, which lightens in dark and
   darkens in light); `rgba(0,0,0,…)` elevation shadows and modal scrims stay literal
   (correct in both themes); entity/series identity hexes (account tints, chart palettes)
   stay theme-invariant by design. **The Wealth Briefing card deliberately remains a dark
   "midnight" hero card in both themes** — it is fully self-contained (own bg + text tiers)
   and reads as an intentional dark-on-paper accent in light mode.

## Consequences

- New color surface work MUST provide a light value for any new forked-role token, or inherit
  an existing token; a bare dark hex in component CSS is a regression.
- `tests/theme.test.ts` pins: default-dark, persistence, system resolution, subscriber
  notification, every forked token lands in the light block and only color/shadow fork.
  The existing byte-identity drift guards cover the regenerated artifacts.
- The static PWA `manifest.webmanifest` keeps its dark `theme_color`; the live
  `<meta name="theme-color">` is what UAs track in-session.
