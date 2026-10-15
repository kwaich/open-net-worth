# A phone chart gets a different viewBox, not a stretched one

- **Date:** 2026-07-30
- **Status:** Accepted
- **Area:** frontend / Commitments tab / mobile

## Context

The Commitments cushion chart is an SVG with `viewBox="0 0 760 152"`, `width: 100%`, no height,
and `preserveAspectRatio="none"`. A viewBox'd SVG renders at *width ÷ aspect-ratio*, so at a
390px phone the chart was ~86px tall and the payments-out / funding-in bars — a 20-unit span of
a 152-unit box — collapsed to ~11px of unreadable nubs. The owner reported it as "the bars are
unreadable".

The first fix was a one-line CSS height pin inside the existing `≤560px` container query:

```css
.c2-chart-svg { height: 168px; }   /* WRONG — reverted */
```

It looked correct in the stylesheet and passed lint, type-check, the test suite, and
`make ui-gate` (44/44). It was still wrong. With `preserveAspectRatio="none"` a pinned height
stretches the **Y axis alone**: `<circle r="2.6">` data points became ellipses, stroke geometry
skewed, and the 24-unit gap between the plot floor (`A_BOT = 104`) and the bar baseline
(`baseB = 128`) scaled up with everything else, so the bar band visibly detached from the plot
it belongs to. The owner caught it immediately in a screenshot.

Nothing in the automated gate could have caught this. Every check we run is structural —
"is there a rule", "does it parse", "does the page overflow" — and a stretched chart overflows
nothing and violates no contract. It is only visible to an eye.

## Decision

**A narrow chart gets its own viewBox, chosen so the rendered scale stays near-uniform. Never a
pinned CSS height on a `preserveAspectRatio="none"` SVG.**

`useNarrow()` (`components/tabs/commitments/useNarrow.ts`, `matchMedia('(max-width: 560px)')`,
matching the tab's container-query breakpoint) drives the geometry constants in `chart.tsx`:

| | desktop | narrow |
|---|---|---|
| `W × H` | 760 × 152 | 360 × 180 |
| plot floor `A_BOT` | 104 | 116 |
| bar baseline `baseB` | 128 | 132 |
| bar span | 20 | 28 |

At 312px of available width the narrow box renders 156px tall at a ~0.87 scale on **both** axes
— honest geometry, dots stay round, and the bar band sits tight under the plot. Desktop is
byte-identical.

A `NOTE:` comment sits at the old pin site in `commitments.css` so the next person doesn't
re-add the one-liner.

## Consequences

- Chart layout constants are now JS, not CSS. A future third breakpoint means another column in
  that table rather than another media query.
- `useNarrow` returns `false` for a non-DOM render, so tests and any SSR path get the desktop
  geometry — the historical behaviour.
- The same hook now also drives the phone-only cash-floor legend entry, since the in-plot axis
  labels were dropped at that width for occluding the series.

## Agent provenance

Found and fixed by an agent during a mobile UI review at the owner's request. The failure worth
recording is not the CSS: it is that the agent reported "fixed" on the strength of a green
build and a passing geometry gate, having never rendered the page. **A visual change is not
verified until it has been looked at.** `frontend/qa/shoot-commitments-mobile.mjs` was added in
the same change so that looking is one command, and so the commitment *detail* screen — which
`scripts/visual-qa.mjs` never opens, and which was carrying a ~85px horizontal overflow nobody
had measured — is covered at all.
