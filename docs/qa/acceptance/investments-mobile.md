# Acceptance — Investments tab on a phone

**Feature:** mobile information density for the Investments tab (Holdings sub-tab).
**Spec:** `docs/superpowers/specs/2026-07-30-investments-mobile-density-design.md`
**Phone breakpoint under test:** `< 640px` (drive at 390×844; also spot-check 360×800).
Pane-responsive card behavior is additionally checked by narrowing the Investments pane
inside a desktop viewport (for example, by opening Lucius).
**Unit coverage:** `frontend/src/components/tabs/investments/mobile-density.test.tsx`,
`frontend/src/components/tabs/mobileViews.responsive.test.ts`.

Every criterion below is an _interaction_, not a render check. "It renders" is not a pass.

## A — Masthead (D1)

- **A1** At 390px the summary bar is a **single** cell. There is no 2×2 grid and no
  cell labelled `TODAY`.
- **A2** The cell reads, top to bottom: `MARKET VALUE`, the hero figure, then
  `+<change> <pct> today` in the good/bad colour.
- **A3** Below a hairline rule, one meta line shows `P&L <signed> <pct>` and
  `<pct>% of net worth`, separated by a `·`. It wraps rather than overflowing at 360px.
- **A4** Today's change appears **exactly once** on the whole screen — cross-check
  against the brief (B1).
- **A5** With hide-amounts on (`body.hide-amounts`), the hero, the today figure and the
  P&L figure all mask; the `% of net worth` percentage does not.
- **A6** Resize to 800px: the 4-metric hero returns as one compact 1×4 row, with `% of
net worth` beneath Market Value plus dedicated Today, YTD, and Unrealized cells.

## B — Portfolio brief (D2)

- **B1** At 390px the card shows **no** `TODAY` stat block.
- **B2** The card has no metric stats band at any width; Today and YTD live in the masthead.
- **B3** The lead tag and narrative are always visible. The secondary bullets are hidden
  behind a `▸ N more` button, closed on load.
- **B4** Tapping `▸ N more` reveals the bullets, rotates the caret, and relabels the
  button `Show less`; `aria-expanded` flips `false → true`. Tapping again re-collapses.
- **B5** The button is reachable and operable by keyboard (Tab to it, Enter/Space
  toggles) and shows a visible focus ring.
- **B6** The drivers strip still scrolls horizontally and its chips still open the asset
  detail on tap.
- **B7** At 800px the brief remains insights-only, and the bullets are shown unfolded with
  no toggle.
- **B8** If Lucius narrows the Investments content pane to ≤760px on desktop, the brief
  folds secondary insights behind the existing `N more` disclosure and shows no metric
  stats; Today and YTD remain in the masthead.

## C — Holdings toolbar (D3)

- **C1** At 390px the toolbar is **one row** and there is **no** chip row beneath it.
  The page does not scroll horizontally.
- **C2** The row holds, in order: search icon-button, asset-class select, account select,
  grouping icon-button. The two selects share the leftover width evenly.
- **C3** The asset-class select's options carry the counts (`All 35`, `Equities 24`, …)
  and match what the chips showed at desktop width.
- **C4** Choosing `Crypto` filters the feed to crypto rows and the selection survives a
  scroll; choosing `All` restores the full list.
- **C5** The card/table density toggle is **absent** from the toolbar and from the
  accessibility tree.
- **C6** The feed renders as cards even when `localStorage.inv-holdings-density` is
  `"table"`. After resizing back to 800px the table view returns — the stored preference
  was never overwritten.
- **C7** At 360px all four controls still fit on the row with no clipping or overlap.
- **C8** The grouping button still opens its picker and still shows its active dot when
  a grouping other than "No grouping" is selected.
- **C9** In a narrow desktop/Lucius pane, asset type and account merge into the same filter
  button used on mobile; the separate account select and type-chip row disappear. Search,
  grouping, and Card/Table remain on one toolbar row.

## D — Holdings cards (D4)

These compact-card criteria apply whenever the Investments content pane is ≤640px,
including a desktop viewport narrowed by Lucius.

- **D1** No figure is ellipsised. Specifically, no card shows a truncated money string
  such as `S$69,135.…` or `+S$1…`. Verify against the largest holding in the book.
- **D2** Card layout: symbol + company name on the left; value on the right with the
  weight beneath it; then a hairline rule; then last price + today's % badge on the left
  and unrealized P&L + P&L % on the right.
- **D3** Values, weights and P&L figures are **right-aligned to the same edge**, so they
  line up vertically when scanning down the list. Check across at least four cards of
  differing magnitude.
- **D4** The rule above the footer band is continuous across the full card width — no gap
  in the middle where the two cells meet.
- **D5** A very long company name truncates with an ellipsis; the value beside it does not
  shrink or wrap.
- **D6** The card-mode column header (`Holding / Value / Weight / P&L / Today`) is hidden.
- **D7** Tapping a card opens the asset detail; the `@`-mention entity row still resolves
  for a single-account holding.
- **D8** Today's badge keeps its up/down/flat colour treatment and stays legible for a
  four-digit percentage.

## E — Regression

- **E1** At a genuinely wide Investments content pane the desktop layout is unchanged;
  narrowing that pane to ≤640px activates the complete compact card even when the browser
  viewport remains desktop-wide.
- **E2** The other five sub-tabs (Performance, Income, Activity, Options, Equity
  Compensation) are untouched at every width.
- **E3** The sub-tab strip still scrolls horizontally on a phone and does not push the
  page wider than the viewport.
- **E4** The empty-portfolio state still renders `EmptyHoldings` with no masthead or brief.
- **E5** Today's Drivers is always one horizontal row. When the chips do not fit, the strip
  scrolls horizontally with no visible scrollbar instead of wrapping.
