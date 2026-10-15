# Acceptance Criteria — Investments Foundation (lane/foundation)

**RFC:** `docs/rfc/investments/00-foundation.md`
**Branch:** `lane/foundation`

---

## AC1 — Masthead + sparkline (≥1020px viewport)

GIVEN the user has holdings in their portfolio  
AND the viewport width is ≥1020px  
WHEN the user opens the Investments tab  
THEN the masthead shows:

- "Total Value" hero cell with the aggregated investment value
- A 30D sparkline graph inside the hero cell with a "30D" cap label
- "Today" cell showing today's value and percentage change
- "YTD vs {benchmark}" cell showing portfolio return, benchmark return, and points ahead/behind
- "Unrealized P&L" cell showing total unrealized gain/loss
- A quiet subtitle under Total Value showing investments as a percentage of net worth,
  without repeating the total-net-worth amount
  AND the sparkline stroke color matches the sign of the 30-day change (green = positive, red = negative)

## AC2 — Masthead responsive (narrow desktop content pane)

GIVEN the Investments content pane is ≥640px and narrowed by Lucius or the browser
WHEN the user views the Investments masthead  
THEN:

- The four masthead cells stay on one compact 1×4 row (never wrap or stack)
- Type and spacing stay readable without horizontal overflow
- The sparkline (`.mk-spark-wrap`) is hidden
- Market value, Today, YTD vs benchmark, and Unrealized P&L remain accessible
  AND below the phone breakpoint the dedicated single-cell phone masthead remains in use.

## AC3 — Stat info tooltip (keyboard + hover)

GIVEN a stat label with a `.stat-info` serif-italic `i` trigger  
WHEN the user hovers over the `i` button  
THEN the `.stat-pop` explanatory popover appears  
WHEN the user moves the cursor away  
THEN the popover dismisses  
WHEN the user keyboard-focuses the `i` button (Tab key)  
THEN the popover appears  
WHEN focus leaves the button  
THEN the popover dismisses  
AND the `i` glyph renders in Georgia serif italic (via CSS class, not inline style)

## AC4 — Violet migration (Lucius identity)

GIVEN the Intel brief card is rendered  
WHEN the user views it  
THEN:

- The `.lx-mark` spark indicator is violet (#7C6CF5), not amber
- The `.intel-brief-ask-spark` ✦ glyph on the "Ask Lucius" button is violet, not amber
- The `.lx-pulse` pulsing thinking indicator is violet, not amber
- No genuine warning/caution surface changed color (e.g., budget warnings, alert badges remain amber)

## AC5 — Empty state shell

GIVEN the user has no investment holdings  
WHEN the user opens the Holdings sub-tab  
THEN the `.mk-empty` empty state renders with:

- An icon area
- A title
- A body description
- Action button(s)
  Within the `.mk-panel` shell container

## AC6 — Sub-tab persistence

GIVEN the user is on the Investments tab viewing any sub-tab  
WHEN the user navigates away and returns  
THEN the previously selected sub-tab is restored (localStorage persistence)  
AND the masthead remains visible on all sub-tabs

---

## Notes

- `make qa-e2e` should be run for AC1–AC6 once the browser-use harness is wired
- `make ui-gate` should be clean (no layout overflow, no unexpected clipping)
- Screenshot evidence required: (1) masthead 4-up wide, (2) masthead narrow 1×4, (3) info tooltip open, (4) violet lx-mark on Intel card
