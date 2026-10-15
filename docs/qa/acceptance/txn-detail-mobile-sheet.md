# Acceptance — Transaction detail drawer as a bottom sheet on phones

Owner report (task 176): the register's transaction drawer rendered on a phone as the desktop
right-docked drawer — a 384px panel on a 390px viewport, leaving a hairline of register beside
it, every touch target pinned into the far corners, the close ✕ scrolling away, and a 7px
`col-resize` strip nobody can grab. Verdict: *"it's too hard to interact"*.

Scope: `frontend/src/components/tabs/accounts/TxnDetail.tsx` + its `.acct-detail*` CSS. Nothing
else uses those classes, and **desktop is unchanged**. The breakpoint is the existing phone one
(`max-width: 640px` ⇔ `useIsMobile()`); both halves of the change key off the SAME breakpoint —
the JS half (which chrome renders) and the CSS half (geometry) can never disagree.

Precedent followed, not invented: the Modal's own `<640px` bottom-sheet tier (92dvh cap, rounded
top corners, slide-up) and the mobile shell's `.mb-sheet` (grab handle + 40px drag-to-dismiss).

Verify at **390×844** on a disposable QA instance — `node frontend/scripts/shoot-txn-sheet.mjs`
boots one (ONW_QA_MODE, /tmp dirs, never `:8080`/`:5174`/live data), asserts TS1.1–TS3.3's
geometry and writes the screenshots below.

## TS1 — It is a bottom sheet, not a drawer
- **TS1.1** GIVEN the viewport is < 640px WHEN a register row is tapped THEN the detail panel is
  **full-bleed** (spans the whole viewport width) and **docked to the bottom edge** — no strip of
  register beside it. *(shoot checks a, b)*
- **TS1.2** GIVEN the sheet THEN it is capped at **92dvh** and sized to its content, so the page
  it came from stays visible above it and the sheet never claims the full screen. *(check c)*
- **TS1.3** GIVEN the sheet THEN its **top corners are rounded** (`--radius-2xl`), its bottom
  corners square, and it rises with a translateY(100%) slide. *(check d1)*
- **TS1.4** GIVEN the sheet WHEN it opens THEN it covers the mobile tab bar (it is a modal
  surface, `z-index: 120` over the bar's `--z-dropdown`) and the page behind it does not scroll.
- **TS1.5** GIVEN the sheet THEN there is **no page-level horizontal overflow** in either View or
  Edit mode. *(checks f, h)*

## TS2 — Every affordance is reachable with a thumb
- **TS2.1** GIVEN the sheet THEN a **grab handle** sits at the top centre, and the pointer-only
  `col-resize` strip is NOT rendered. *(check d2)*
- **TS2.2** GIVEN the grab handle WHEN it is dragged down more than 40px THEN the sheet
  dismisses; a tap or a shorter drag does NOT. *(unit: `TxnDetail.mobileSheet.test.tsx`)*
- **TS2.3** GIVEN the sheet is in **Edit** mode WHEN the handle is dragged down THEN it returns
  to View mode instead of discarding the edit — mirroring the ✕ and the (already protected)
  backdrop, which never closes out of Edit. *(unit)*
- **TS2.4** GIVEN a long transaction (source email, activity timeline) WHEN the body is scrolled
  to the end THEN the grab handle and the kicker/✕ row **stay pinned** to the sheet's top edge.
  *(check l)*
- **TS2.5** GIVEN the footer THEN "Edit transaction" / "Delete" (and "Save changes" / "Cancel" in
  Edit mode, and Approve/Reject on a needs-review card) are each **≥44px** tall, and the footer's
  bottom padding includes `env(safe-area-inset-bottom)` so nothing sits under the home indicator.
  *(check e)*
- **TS2.6** GIVEN the sheet is scrolled to its end WHEN the scroll continues THEN it does not
  chain to the page behind (`overscroll-behavior: contain`).

## TS3 — Desktop is untouched
- **TS3.1** GIVEN the viewport is ≥ 640px THEN the drawer is still right-docked, full height, at
  its remembered width (default 384px). *(checks i, j)*
- **TS3.2** GIVEN the desktop drawer THEN the drag-to-resize handle is present and the grab
  handle is absent; the resize + `localStorage` width memory behave exactly as before.
  *(check k + `TxnDetail.resizeDrag.test.tsx`, unchanged)*
- **TS3.3** GIVEN the desktop drawer THEN `--detail-drawer-w` is still published so the floating
  Lucius dock shifts clear of it; on a phone it is NOT published (there is no right-docked drawer
  to clear, and `.lx-dock` is `display: none` there anyway).

## TS4 — Gates
- **TS4.1** `node frontend/scripts/shoot-txn-sheet.mjs` — all checks pass. Screenshot evidence:
  `txn-sheet-mobile-view.png`, `txn-sheet-mobile-scrolled.png`, `txn-sheet-mobile-edit.png`,
  `txn-sheet-desktop-view.png` (in `/tmp/onw-visual-qa`).
- **TS4.2** `npm run lint:classes` clean — `.acct-detail-grab` / `.acct-detail-grabbar` are
  defined in `accounts.css` in the same change.
- **TS4.3** `make ui-gate` clean; `npx vitest run` green (incl. the 5 new
  `TxnDetail.mobileSheet.test.tsx` cases); `npx tsc --noEmit` clean.
