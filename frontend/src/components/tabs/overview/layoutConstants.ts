// Shared visual constants between grid.ts (pure math), DashboardGrid.tsx
// (gesture handling) and GridCard.tsx (rendering). Ported from the design
// handoff's `app.jsx` (`GAP_Y`, `SNAP`, `autoScroll`'s `M`/`MAX`) — the
// handoff's row-unit model (`ROW_UNIT=2`) is dropped here (grid.ts's `h` is
// already a literal px value, not a row count), so these are plain pixels.

/** Visual gap reserved between grid cells — GridCard subtracts this from its
 * rendered height so stacked/adjacent cards don't touch edge-to-edge. */
export const GAP_PX = 16

/** Resize snap-to-edge threshold, in px — a dragged resize edge within this
 * distance of a neighboring widget's top/bottom edge snaps to it. */
export const RESIZE_SNAP_PX = 10

/** Edge auto-scroll: while dragging/resizing within this many px of the
 * viewport top/bottom, the page scrolls; MAX is the top scroll speed
 * (px/frame), reached at the edge itself and easing to 0 at the margin. */
export const EDGE_SCROLL_MARGIN_PX = 44
export const EDGE_SCROLL_MAX_PX = 6

/** A collapsed (non-section) widget renders as a slim title-only bar at this
 * FINAL rendered height (52px, matching the handoff's own collapsed-bar
 * size). GridCard always subtracts GAP_PX from whatever `h` it's given
 * (`height: h - GAP_PX`, so stacked cards don't touch edge-to-edge) — this
 * constant has to pre-add that back, or the bar renders 16px SHORTER than
 * intended (36px instead of 52px). */
export const COLLAPSED_WIDGET_H_PX = 52 + GAP_PX

/** A newly-added widget/section gets an accent-ring pulse for this long. */
export const NEW_ITEM_PULSE_MS = 1900
