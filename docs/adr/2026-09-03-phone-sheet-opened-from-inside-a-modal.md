# 2026-09-03 — A phone sheet opened from inside a modal is its own layer: pointer, stacking, gesture, Escape

**Status:** accepted · APPLIES the 2026-07-28 rule (a surface portaled to `<body>` while a Radix
modal is up must restore its own pointer events) to the shared `kebab-sheet` classes, and adds
the two things that rule did not have to cover: a nested sheet's GESTURE and its ESCAPE.
**Where:** `frontend/src/index.css` (`.kebab-sheet`, `.kebab-sheet-scrim`),
`frontend/src/components/tabs/plan/CategoryKindFilter.tsx` (`isolatePointer`,
`data-modal-escape-layer` on the in-modal wrapper), `plan.css` (`.pl-catfilter-item*`)
**Tests:** `CategoryKindFilter.test.tsx` (3 in-modal cases), `__tests__/kebab-sheet-layer.test.ts`
(pins the CSS), `qa/catfilter-in-modal.mjs` (real Chromium, 390×760, 24 checks)

## Context

The Kind filter in Manage categories opens as a `.kebab-sheet` portaled to `<body>` — from INSIDE
the Manage categories `PlanningModal`, a Radix modal Dialog. On the owner's iPhone: taps on the
filter fell through to the parent (the X could not be tapped at all; a scrim tap dismissed the
parent instead), no scrim was visible over the parent sheet, and the rows read "All39".

Four independent causes, each verified in Chromium before the fix:

1. Radix `modal` mode sets `body { pointer-events: none }`; a portaled child inherits it unless it
   restores `pointer-events: auto` itself (ADR 2026-07-28, same mechanism). `elementFromPoint` on
   the scrim hit `.pl-clabel`, on a row hit `.pl-catmgr-row`.
2. `.kebab-sheet-scrim` sat at `--z-overlay` (300) while the parent's `.pl-ov` is `--z-modal`
   (500) — the sheet was at 500 but its scrim painted BEHIND the modal it was opened from.
3. The sheet stays a React descendant of the modal, and React synthetic events bubble through
   portals — a drag on the child sheet also drove the parent's `useSheetDragToDismiss`.
4. Radix's Escape listener is a document capture listener registered when the modal mounted, so
   it fires before the child's; it yields only when `[data-modal-escape-layer]` is in the modal's
   own DOM subtree, and the sheet is not (it lives on `<body>`).

## Decision

- **Pointer + stacking are fixed at the shared-class layer.** `.kebab-sheet` and
  `.kebab-sheet-scrim` carry `pointer-events: auto` and both sit at `--z-modal`. Every
  kebab-sheet consumer (ActionMenu, KebabMenu, HoldingsFilterSheet, CategoryKindFilter) gets this
  for free; DOM order (portaled last → painted last) settles the tie with the parent overlay.
- **Gesture isolation is per consumer, at the sheet root and scrim:** `isolatePointer(handlers)`
  wraps the drag handlers and calls `stopPropagation()` on every pointer event. It also stops the
  native event; Radix's outside-press check survives because it flags "inside" in the CAPTURE
  phase (`onPointerDownCapture`), before any bubble handler runs.
- **Escape is yielded by marking the in-modal WRAPPER**, not the sheet:
  `data-modal-escape-layer` goes on `.pl-catfilter-wrap` (which IS in the modal's subtree) while
  the sheet is open, and comes off with it, so the next Escape closes the modal as before. The
  desktop popover keeps the marker on itself — it is portaled INTO `.pl-modal`.
- **Row shape:** label · fixed-width tick slot (`1.1em`, always rendered, empty when unselected)
  · right-aligned tabular count. The slot is always present so the tick never shifts the count
  column (owner: "I don't like the fact that the check icon shift the numbers"). It sits LEFT of
  the count (owner: "Why the tick doesn't be on the left of the numbers?"). It stays a tick, not
  a checkbox: the filter is single-select, a pick closes the sheet, "All" is the whole set.

## Alternatives rejected

- **Capture-phase isolation** (`onPointerDownCapture` on the sheet) — would ALSO cut Radix's own
  capture flag off, making every tap inside the sheet an "outside press" that dismisses the
  parent. Bubble-phase is the only phase that runs after Radix has taken its note.
- **Marking the sheet itself with `data-modal-escape-layer`** — invisible to the modal's
  `querySelector` on its own subtree; the wrapper is the only element the modal can see.
- **Scrim at `--z-overlay` with `.pl-ov` lowered** — the token scale is a closed enum (base /
  sticky / dropdown / overlay / modal / toast); a nested sheet belongs at the same tier as the
  sheet it is nested in, not in a new tier between them.
- **Checkboxes instead of the tick** — misrepresents a single-select control as multi-select.
  Multi-select is a separate product change if wanted.

## Consequences

- The next `.kebab-sheet` consumer opened from inside a modal needs only the last two pieces
  (`isolatePointer` at its sheet + scrim, the marker on its in-modal wrapper); the CSS is already
  right. None of the other three consumers is rendered inside a modal today.
- `isolatePointer` hides pointer events from any FUTURE bubble-phase document listener registered
  elsewhere in the app — nothing today; a listener that needs to see gestures inside a nested
  sheet must use capture.
- Safari/iOS was NOT run for this change; Chromium touch emulation and the vitest suite are the
  evidence. Owner verification on the phone is the acceptance step.

## Amendment — 2026-09-26: the drawer over a modal shares the modal tier; gesture ownership is the hook's

Owner report (iPhone): in review mode, a receipt tapped in the transaction drawer opened its viewer
BEHIND the drawer, and touching the drawer then slid it down. Reproduced in Chromium
(`qa/txn-drawer-nested-modals.mjs`) — the same two of the four causes above, on a different pair:

- **Stacking (cause 2).** `.acct-detail-wrap.above-modal` — the drawer opened over the review cards
  or the import modal — sat at `--z-toast` "so it wins regardless of DOM/mount order". It won
  against every dialog opened FROM it too (receipt and statement viewers, the unlink and delete
  confirms), all at `--z-modal`. It now sits at `--z-modal`, the rule this ADR set for a nested
  layer: opened later, painted later — over the modal it came from, under anything opened from it.
- **Gesture (cause 3).** The viewer is a React child of the drawer, so its pointer events reached
  the drawer's `useSheetDragToDismiss`, which took the pointer capture from the viewer and dragged
  (or dismissed) the drawer instead. `useSheetDragToDismiss` now ignores a pointerdown that did not
  start inside the DOM element its handlers are on, so every sheet owns only its own gestures and
  no nested surface needs to opt in. `isolatePointer` in `CategoryKindFilter` stays (harmless), but
  a new nested sheet no longer needs it for the gesture. Its Escape marker is still required.

Not changed, and seen while verifying: the drawer is a NON-modal Radix dialog, so it dismisses
itself when focus moves outside it — a dialog the host opens beside it rather than inside it
(`TransactionSurfaces`' delete confirm) closes the drawer as it opens, so Cancel does not return
to it. The behaviour is the same before and after this amendment.

## Amendment — 2026-09-26, later: a dialog stacked on the drawer never closes it

The paragraph above, changed the same day at the owner's request. `TxnDetail` now treats an
interaction inside a dialog portaled AFTER the drawer (`isStackedOnDrawer` — the same
opened-later order its tier relies on) as not outside, so Radix's non-modal dismiss no longer
fires for it: the delete confirm opens over the drawer and Cancel returns to it. The page behind,
Lucius and a modal the drawer was itself opened over still dismiss it, exactly as before. The
consequence to know: ANY dialog opened while the drawer is up — the command palette too — now
stacks on it instead of closing it. The add-category modal was never affected in practice (focus
was last inside the drawer, in the category search it replaced); its test pins that the edit
survives regardless. Tests: `TxnDetail.stackedDialogs.test.tsx`; `qa/txn-drawer-nested-modals.mjs`
checks 7–11.
