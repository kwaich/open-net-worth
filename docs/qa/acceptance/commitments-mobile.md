# Acceptance — Commitments on mobile (390px, touch)

Scope: the Commitments **list** (masthead + needs lane + rows) and a commitment **detail**
screen, at phone width with a coarse pointer.

Harness: `node frontend/qa/shoot-commitments-mobile.mjs` — boots the disposable QA instance,
drives `390×844` with `isMobile`/`hasTouch` so `(hover: none)` and `(pointer: coarse)` resolve
the way they do on a real phone, opens the first commitment, and prints measured geometry plus
screenshots. `make ui-gate` does NOT cover this: `scripts/visual-qa.mjs` only visits tab routes
and never opens a commitment, so the detail screen had no coverage at all before this file.

## Criteria

### A. Money is stated in one currency
- **A1** On a non-base-currency commitment (e.g. ₫ VND), the detail stat strip's **Paid**,
  **"x% of <total>"** and **Next payment** are base-currency figures — the same currency as
  **Remaining**. *Regression: they were native-currency sums printed with the base symbol, so a
  ₫ commitment read "Paid S$1,922,829,852.00" beside "Remaining S$388,465.36".*
- **A2** `paidBase + remainingBase === nativeTotalBase` for the same commitment.
- Covered by `frontend/src/utils/commitmentsBase.test.ts`.

### B. No horizontal overflow
- **B1** List: `documentElement.scrollWidth <= innerWidth + 1`.
- **B2** **Detail**: same. *Regression: the timeline strip's absolutely-positioned amount labels
  and its (opacity-0 but still laid-out) `.c3-tip` tooltips pushed the page ~85px wide.*
- Measured: both `scrollWidth=390 innerWidth=390 overflow=none`.

### C. Nothing is reachable by hover alone
- **C1** Tapping a cushion-chart month opens its breakdown; tapping it again, tapping outside,
  or Escape dismisses it. *Was `onMouseEnter` only — unreachable on touch.*
- **C2** The chart tooltip stays inside the card at every month index and every width.
- **C3** Funding-coverage bar segments are `<button>`s — tap or keyboard-focus opens the sources
  popover. *Were `<div>`s with `onMouseEnter` only.*
- **C4** Inline edit/delete affordances (`.c2-iconbtn`) are visible under `(hover: none)`.
  *Were `opacity: 0` revealed only on `:hover`, so invisible on a phone forever.*

### D. Chart is legible, and honest
- **D1** The chart renders ~156px tall at 390px (measured `312×156`), not ~86px.
- **D2** It gets there via a **taller viewBox** (`useNarrow()` in `chart.tsx`), never a pinned
  CSS height — `preserveAspectRatio="none"` makes a pinned height stretch Y alone, which turns
  the data dots into ellipses and detaches the bar band from the plot. Do not reintroduce it.
- **D3** The payments-out / funding-in bars sit on the baseline directly under the plot.
- **D4** The red `.gap` month label (where cash goes short) is never hidden by axis thinning.
- **D5** No axis label paints over the series: `$0 cash` / `cash floor` are dropped at this
  width and the floor is stated in the legend instead.

### E. Reading order matches reading priority
- **E1** Card band order on phone is **verdict → suggested fix + action → chart → stats**.
  *Was verdict → stats → chart → fix, which put four reference numbers (commonly three of them
  `S$0.00`) between the verdict and the only tappable recommendation, pushing the fix off the
  fold.*
- **E2** All four stats still render at every width — a zero is a real answer ("nothing due in
  30 days"); they are demoted in space, not removed.
- **E3** No stray part-width divider between wrapped stat rows.

### F. Touch targets ≥ 44px (`--tap-min`)
Measured at 390px: filter rail `306×44`, sort `44×44`, detail sub-tab `79×38` in a 44px rail.
- Filter segmented control · sort select · timeframe pills · needs-lane buttons ·
  `.c2-iconbtn` · detail sub-tabs.

### G. Density
- **G1** The `COMMITMENT / REMAINING` table header is hidden on phone (measured `0×0`).
- **G2** The timeline strip renders as a chronological **list** (measured `320×99`), not the
  lollipop (`0×0`), at ≤640px **or** on any `(hover: none)` device.
- **G3** Needs-lane two-button rows right-align when they wrap.
- **G4** The verdict sentence wraps instead of ellipsing between 440–720px.
- **G5** Next-payment rows give the label its own line rather than truncating to "4th · 1…".
- **G6** The detail stat strip is a 2×2 — no orphaned fourth stat on its own line.

## Known not-covered
- Real-device Safari/iOS (harness is Chromium emulation).
- `qa/browser-use` spec for these flows — not written; this file + the probe script are the
  current gate.
