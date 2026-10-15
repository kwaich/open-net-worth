---
number: 2026-08-31
title: Privacy mask extended to canvas charts via live body class checks and MutationObserver redraw in the shared OvChart seam
status: Accepted
date: 2026-08-31
deciders: Antigravity (fix implementation) + repo owner (implementation plan approval)
---

# 2026-08-31. Privacy mask extended to canvas charts via live body class checks and MutationObserver redraw in the shared OvChart seam

## Context

Privacy mode in Open Net Worth is triggered via the `H` key, the sidebar "Hide amounts" button, or
the mobile More sheet. It sets `document.body` class `hide-amounts` (persisted in
`localStorage.wealthHide`).

CSS masks any DOM node carrying `.money` (via `<Money>` and `moneyClass()`) by overlaying `***`.
However, charts rendered via Chart.js onto `<canvas>` elements draw their axis ticks and tooltip
labels directly as raster pixels, which CSS selectors cannot reach.

Previously, `kTick()` and widget tooltip callbacks rendered real formatted amounts (e.g. `S$98k` on
the y-axis and exact `S$98,234.56` on hover). Furthermore, `OvChart` only rebuilt when its `sig`
prop changed, meaning toggling privacy mode had no mechanism to trigger a repaint of the canvas.

## Decision

We extend the privacy mask to canvas charts at the single shared wrapper seam
(`frontend/src/components/tabs/overview/charts.tsx`):

1. **Live DOM state check at draw time**:
   - `amountsHidden()` checks `document.body.classList.contains('hide-amounts')`.
   - `kTick(v)` returns `'***'` when `amountsHidden()` is true.
   - `moneyTip(v)` returns `'***'` when `amountsHidden()` is true, and `fBase(Number(v))` otherwise.
   Because Chart.js calls tick and tooltip callbacks at draw time, no React state wrapping is needed
   for formatting.

2. **MutationObserver on `document.body` class flips**:
   - `OvChart` observes `document.body` for attribute changes (`attributeFilter: ['class']`).
   - When the `hide-amounts` class is added or removed, it invokes `inst.current?.update()` to force
     a redraw without tearing down or rebuilding the canvas instance.

3. **Adopt `moneyTip` in Net Worth chart tooltips**:
   - `frontend/src/components/tabs/overview/widgets/netWorth.tsx` uses `moneyTip` for both trend and
     composition tooltip callbacks.

## Consequences

### Positive
- All Overview charts sharing `kTick` / `axes()` automatically mask currency y-axes to `***` under
  privacy mode with zero extra per-widget logic.
- Hovering the Net Worth chart renders `***` instead of leaking full-precision numbers.
- Privacy toggle repaints immediately and seamlessly without requiring page reloads or recreating
  Chart.js instances.
- Reversible: turning privacy mode off immediately restores compact axis ticks and full tooltips.
- The visual trajectory line and area fill remain intact, preserving chart context without leaking
  private figures.

### Negative / Trade-offs
- A MutationObserver is attached for each mounted `OvChart` instance; however, observation is
  filtered to `class` changes only and cleanly disconnected on unmount, incurring negligible overhead.
- Observer triggers on unrelated body class changes (such as theme toggles), causing a harmless
  single-frame redraw.

## Alternatives Considered

- **Toggling canvas visibility / hiding the whole chart**: Rejected because the shape, trend, and
  date timeline are not secret; masking the numbers (`***`) matches the DOM `.money` aesthetic and
  preserves screen layout.
- **Passing a `privacyMode` prop through React state down into all charts**: Rejected because
  Chart.js operates imperatively outside React render cycles. Live DOM reading in callback formatters
  combined with a MutationObserver in `OvChart` cleanly centralizes the mechanism at the wrapper
  seam with zero prop drilling across widget definitions.

## Agent Provenance

- **Decided by**: Antigravity + repo owner
- **Branch / lane**: `fix/privacy-chart-mask`
- **Driving artifact**: `implementation_plan.md` (a contributor's local plan, not in the repo)
- **Why this way**: Centralizes the fix into the single `OvChart` / `charts.tsx` seam, ensuring all Overview charts gain privacy masking consistently with no duplicate lifecycles.
