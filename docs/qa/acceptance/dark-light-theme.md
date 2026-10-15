# Acceptance — Dark / light theme (design-system-wide)

Two-theme token pipeline + per-device preference (ADR `2026-07-21-two-theme-token-pipeline`;
spec `docs/superpowers/specs/2026-07-21-dark-light-theme-design.md`). Verify on the running
app via `make qa-e2e` / `make qa-cu`, never `:5174` / `:8080` directly.

**The bar:** light mode is a designed warm-paper palette (ivory `#edeae3` page — never plain
white), not an inverted afterthought; dark remains byte-identical for an install that never
touches the setting.

Backend: none. Frontend: `src/design/tokens.source.mjs` (+ light values) ·
`scripts/build-tokens.mjs` (light block emission) · `src/theme.ts` (preference state) ·
`index.html` (pre-paint stamp) · `components/tabs/settings/AppearanceSettings.tsx` (control) ·
`utils/chartTheme.ts` (canvas charts).

## Criteria

- **DLT-1 Toggle exists and applies instantly.** GIVEN Settings → Appearance, WHEN the user
  picks Light in the Theme segmented control, THEN the whole app re-renders on the warm-paper
  palette with no reload and no Save button; picking Dark restores the original look exactly.
- **DLT-2 Light is warm paper, not white.** GIVEN light mode, THEN the page background is
  `#edeae3` (ivory/stone) and cards are cream — `#ffffff` never appears as the page or panel
  background; body text ink is `#23262e` at ≥ 4.5:1 contrast on every surface tier.
- **DLT-3 Preference persists per device.** GIVEN Light chosen, WHEN the page is reloaded,
  THEN it paints light from the first frame (no dark flash — the pre-paint script stamps
  `data-theme` before stylesheets apply) and `localStorage['onw-theme']` holds the choice.
- **DLT-4 System follows the OS.** GIVEN System chosen, THEN the app matches the OS scheme
  and flips live when the OS scheme changes, without reload.
- **DLT-5 Default is dark.** GIVEN a fresh device (no stored preference), THEN the app renders
  the pre-existing dark palette unchanged.
- **DLT-6 Charts follow the theme.** GIVEN Overview (donut / buckets bar / trend), Plan
  fan/trajectory charts, WHEN the theme flips, THEN tick labels, grid lines, tooltips and the
  net-worth line re-render legibly in the new theme (no near-white lines on paper, no
  near-black on midnight). SVG charts (Plan, sparklines) follow automatically via `var()`.
- **DLT-7 Modals, chat, and status colors hold up in light.** Modals sit on `#f8f6f0`, the
  danger/error surfaces, good/bad/warn badges and pills use the re-tuned darker light-values
  (readable on cream), and the Lucius dock/composer stays legible.
- **DLT-8 Deliberate exceptions.** The Wealth Briefing card renders as its dark "midnight"
  hero design in BOTH themes (intentional); entity identity colors (account tints, chart
  series palette) are theme-invariant.
- **DLT-9 Native controls match.** Form controls, scrollbars and date pickers follow
  `color-scheme` (dark controls in dark, light in light).
- **DLT-10 Brand accent override composes.** GIVEN an instance `accent_color` brand override,
  THEN it applies identically in both themes (it overrides `--accent`, which both themes
  alias to `--primary`).
