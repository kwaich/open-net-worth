# Acceptance — Overview v2 widget dashboard

Faithful port of `frontend/design_handoffs/overview_handoff/` (ADR
`2026-07-22-overview-tab-redesign-faithful-port.md`). Verify on the running app via
`make qa-e2e` / `make qa-cu`, never `:5174` / `:8080` directly.

**The bar this file exists to clear:** the task's two hard requirements were (1) predefined
widgets and Lucius-generated widgets share ONE base implementation — there is no parallel data
model for generated content — and (2) no duplicate/confusable identifier fields
(`instanceId`/`defId` replace the prototype's `uid`/`id`). Every criterion below should be
checkable without reading code — if a check requires "trust me, the types line up," the gate
hasn't actually verified anything.

## OV1 — Unified widget model (predefined widgets are preset instances)

- **OV1.1** GIVEN the default Overview view WHEN it renders THEN every card on the grid — the
  net-worth hero, needs-attention, and an Ask-Lucius card if present — renders through the SAME
  card chrome (title row, kebab menu, drag handle in Customize mode). There is no visual "this
  one is special because Lucius made it" tell beyond the Lucius spark glyph the design itself
  specifies.
- **OV1.2** GIVEN Customize mode is open WHEN the user adds an "Ask Lucius" widget from the
  picker THEN it behaves identically to adding "Net worth (numbers)" — same drag/resize/rename/
  duplicate/remove/kebab-menu affordances, same grid math placing it.
- **OV1.3** GIVEN a duplicated widget (kebab → Duplicate) WHEN inspected THEN it has a NEW
  `instanceId` and the SAME `defId` as its source — two "Movers" widgets coexist without
  colliding.

## OV2 — Add / drag / resize / rename / duplicate / remove

- **OV2.1** GIVEN Customize mode WHEN the user clicks "Add widget" THEN the picker opens grouped
  by tab (Net worth/Overview/Cash flow/Investing/Planning/Reports/Content) with search, and
  picking a tile appends it to the bottom of the grid at its default size.
- **OV2.2** GIVEN Customize mode WHEN the user drags a widget's handle THEN it tracks the pointer
  and other widgets reflow (compaction) on drop; a resize handle drag changes w/h and the widget
  lifts into the topmost open slot in its new column span.
- **OV2.3** GIVEN Customize mode WHEN the user double-clicks nothing (there is no inline rename
  on the card itself) but opens the kebab menu THEN Display / Set alert / Share with Lucius /
  Duplicate / Remove all appear and each works.
- **OV2.4** GIVEN Customize mode is open and the user drags/resizes/adds/removes several widgets,
  THEN clicks **Cancel** THEN the view reverts EXACTLY to its state when Customize was entered
  (the live-edit model — no separate draft was ever created, `Cancel` restores a snapshot).
  Clicking **Save** instead keeps the edits and exits edit mode.
- **OV2.5** GIVEN Customize mode WHEN the user clicks "Add section" and names it THEN a full-width
  divider appears and nothing below it can be dragged above it (hard floor in compaction).

## OV3 — Multiple named views

- **OV3.1** GIVEN the view tab bar WHEN the user clicks `+` THEN a new empty view is created and
  becomes active; its own widgets array is independent of every other view.
- **OV3.2** GIVEN two views with different widgets WHEN switching tabs THEN the whole grid swaps
  (not a filter) — no cross-view widget bleed.
- **OV3.3** GIVEN Customize mode WHEN the user double-clicks the active view tab THEN it becomes
  renameable inline.

## OV4 — Persistence

- **OV4.1** GIVEN any add/remove/drag/resize/rename edit THEN a reload of the page (same login)
  shows the SAME layout — persisted via `GET`/`PUT /api/dashboard/layout`, not `localStorage`.
- **OV4.2** GIVEN the backend is briefly unreachable during a save THEN the UI shows a
  non-blocking error notice ("could not save your dashboard changes") rather than silently
  losing the edit or crashing the tab.

## OV5 — Lucius integration (never a reimplementation of the dock/side panel)

- **OV5.1** GIVEN an "Ask Lucius" widget WHEN the user types a question and submits THEN the
  REAL Lucius side panel/dock opens with that question prefilled — this is verifiable by
  confirming `LuciusDock.tsx`/`state/luciusChat.tsx` were NOT modified by this change (`git diff
--stat` against those paths is empty) and that the widget's "Ask" action calls the existing
  `onChatSuggest` prop, not a new chat surface.
- **OV5.2** GIVEN any widget's kebab menu WHEN "Share with Lucius" is clicked THEN the same real
  panel opens with a message naming the widget — no second chat UI appears anywhere on screen.
- **OV5.3** GIVEN the "Lucius custom" (`lux-html`) widget WHEN the user picks a previously-created
  artifact THEN it renders inside the SAME sandboxed iframe (`sandbox="allow-scripts"`, no
  `allow-same-origin`) `ArtifactCard`/`artifactFrame.ts` already use for chat artifacts — never a
  new/duplicate sandbox implementation.
- **OV5.4** GIVEN the "Lucius widget" (`lux-chart`, spec-driven auto-chart) type in the picker
  WHEN added THEN it shows an honest "coming soon" placeholder — never a fabricated or silently
  broken chart (documented as deferred in `PARITY_AND_GAPS.md`).

## OV6 — Fully responsive on mobile

- **OV6.1** GIVEN a viewport ≤ 640px WHEN the Overview tab renders THEN every widget stacks in a
  single column in y-order, full width, and is fully readable without horizontal scroll.
- **OV6.2** GIVEN a viewport ≤ 640px and Customize mode open THEN drag/resize handles are hidden
  (touch-drag pixel math is not attempted) and each widget instead exposes move-up/move-down
  buttons that reorder it in the stack — full functional parity for rearranging, adapted to touch.
- **OV6.3** GIVEN a viewport ≤ 640px WHEN the Add-widget picker or a Customize-mode action sheet
  opens THEN it remains fully usable (no clipped/overflowing modal, tap targets ≥ the app's usual
  minimum).
- **OV6.3a** GIVEN a viewport ≤ 640px and Customize mode is open WHEN the user switches between
  dashboard views THEN Customize mode remains active, the switch alone does not mark the layout
  dirty, and Add widget/Add section target the selected view. The add controls and view tabs remain
  sticky together as one opaque surface, and a floating dock above the mobile navigation keeps
  Reset all, Cancel, and Save reachable. The dock matches the transaction bulk-action panel's
  height and surface treatment; Reset/Cancel restore edits across every view without moving the
  user away from the selected view when it still exists, and the dock never covers the final widget.
- **OV6.4** GIVEN a widget card resized independently of the viewport (e.g. narrowed to 3 columns
  on desktop) THEN its own internal layout adapts via container queries (e.g. a breakdown
  widget's percentage column hides below ~260px card width) — proving the responsiveness is
  per-card, not just per-viewport.
- **OV6.5** GIVEN the Net Worth card is resized from wide through medium and very narrow
  container widths THEN Today, Unrealized P&L, Cash on hand, and Liabilities remain visible as
  4×1, 2×2, and 1×4 stat grids respectively; the card grows to its measured content height and
  every widget below it repacks with the normal grid gap.
- **OV6.6** GIVEN the Cash Flow card is resized independently of the viewport THEN its In and
  Out labels, bars, amounts, and transaction counts remain visible; at compact widths each bar
  moves to a full-width row under its label and amount.
- **OV6.7** GIVEN any widget's content reflows or changes asynchronously THEN the dashboard
  measures the rendered panel through the shared observer path, batches height updates to the
  next animation frame, and recompacts without persisting a breakpoint-specific height,
  oscillating, overlapping, or emitting a ResizeObserver loop warning.

## OV7 — Empty states, never a lie

- **OV7.1** GIVEN a brand-new install with no accounts/holdings WHEN Overview loads THEN the
  existing "Your wealth dashboard starts here" empty state shows (Add first account / Import a
  statement / Load demo data) — unchanged from the pre-redesign behavior.
- **OV7.2** GIVEN accounts exist but the active view has zero widgets WHEN Overview loads THEN
  "Build this view" shows with both "Add widget" and "Build with Lucius" entry points.
- **OV7.3** GIVEN a widget whose backend data isn't reachable (a genuinely deferred capability,
  see `PARITY_AND_GAPS.md`) THEN it shows a specific, honest placeholder message — never a
  fabricated number or a blank/broken card.
