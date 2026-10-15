---
title: Mobile sheet gesture — native scroll inside sheets; drag-vs-scroll decided once at first movement
status: Accepted
date: 2026-09-03
deciders: Claude (bug-fix task) + repo owner
---

# 2026-09-03. Mobile sheet gesture — native scroll inside sheets; drag-vs-scroll decided once at first movement

## Context

`useSheetDragToDismiss` is the one gesture behind every mobile bottom sheet: Modal's phone mode,
`MobileTabBar`'s `.mb-sheet`, ActionMenu/KebabMenu/filter sheets' `.kebab-sheet`, PlanningModal's
`.pl-modal`, and TxnDetail's `.acct-detail.sheet` — nine consumers sharing one hook.

The prior design had JS own ALL scrolling inside a sheet: `touch-action: none` on every scroll
region, a per-frame scroll/drag handoff inside the pointermove handler (`applyFrame`), JS-simulated
momentum (`startMomentum`), a `scrollConsumed` gate, and raw sample buffering (`rawSamples`). It
grew to ~485 lines and produced a recurring class of iOS bug: scroll fighting drag, a jumpy
scroll→drag handoff mid-gesture, and momentum that didn't feel native. Dismiss thresholds had been
tuned to distance 0.45 / velocity 0.7 to compensate for velocity the JS momentum sim was injecting
into the release — evidence the design was being patched around, not fixed.

Root cause of the original misdiagnosis: the first native-scroll attempt (commit `4b2b93f91`)
called `preventDefault()` on **pointermove** with CSS `touch-action: pan-y`. Per the pointer-events
spec, cancelling pointermove cannot veto native scrolling — only a non-passive `touchmove` listener
(or `touch-action: none`) can. That failure was read at the time as an unwinnable race between JS
scroll and native scroll, and "JS owns everything" was built on that false premise.

The repo already had the correct mechanism proven on the owner's phone:
`useHorizontalOverscrollLock` (`frontend/src/hooks/useHorizontalOverscrollLock.ts`) uses a
non-passive `touchmove` listener that `preventDefault()`s only at a scroll boundary, letting native
scroll run everywhere else. Vaul (the reference drawer library — `[data-vaul-drawer]{touch-action:
none}`, `shouldDrag`, `scrollLockTimeout` = 100ms) and react-aria's `usePreventScroll` (iOS rule:
prevent touchmove when the nearest scrollable is at the boundary in the direction of travel, or
there is none) both use the same contract.

## Decision

We will let sheet content scroll natively and decide drag-vs-scroll once per gesture, mirroring
Vaul / react-aria's contract instead of having JS own scrolling.

1. **Content scrolls natively.** `touch-action: none` lives ONLY on sheet roots and drag handles.
   Per the pointer-events spec / WebKit's `computeEffectiveTouchActions`, a root's `none` does not
   reach descendant scroll containers, so non-scrolling chrome drags while scrollable content keeps
   the browser's own scroll, momentum and rubber-band. A new scrollable region inside a sheet needs
   no CSS opt-in.
2. **Drag-vs-scroll is decided ONCE, at the first movement past a 4px catch threshold
   (`CATCH_THRESHOLD_PX`):** drag iff there is no scrollable ancestor under the finger, or the
   nearest scrollable is at `scrollTop <= 0` and the finger is moving down — and not sideways.
   "Scrollable" means on either axis, and a sideways-dominant movement over anything scrollable
   is the browser's (the categories table pans horizontally on a phone; a wide table that fits
   vertically still owns its pan). Otherwise the gesture is `native` for the rest of that
   gesture — the hook never touches it again. A gesture that scrolled cannot later turn into a
   dismiss.
3. **A non-passive `touchmove` veto (`bindTouchVeto`)**, bound lazily on the sheet root,
   `preventDefault()`s when the nearest scrollable is at the boundary in the direction of travel or
   there is none — react-aria's iOS rule — so a drag the hook has claimed isn't also scrolled or
   overscrolled by the browser underneath it. Its axis is judged on the gesture as a whole (from
   the touch's origin), not per event, so a pixel of vertical wobble on a sideways pan can never
   cancel the horizontal scroll. React's `onTouchMove` is registered passive, so this must be a
   direct `addEventListener(..., { passive: false })`; pointerdown fires before touchstart, so
   binding in `onPointerDown` on `currentTarget` is in time, and Radix unmounts the Modal card
   when closed, so a mount-effect binding would not work. The veto and the drag decision share
   ONE predicate, `sheetOwnsMovement` (`sheetScrollOwnership.ts`), so they cannot disagree.
4. **`SCROLL_LOCK_TIMEOUT_MS = 100`** (Vaul's `scrollLockTimeout`) after a declined gesture stops an
   immediate re-pull from being caught as a drag while a native scroll may still be settling.
5. **Momentum simulation and the mid-gesture scroll→drag handoff are deleted.** `sheetDragPhysics`
   keeps only velocity/threshold/rubber-band/scrim math; dismiss thresholds return to distance 0.35
   of sheet height or velocity 0.5 px/ms — the 0.45/0.7 values existed only to compensate for
   velocity the JS momentum sim was inheriting.
6. **Chrome that lives inside a scroll container opts in as a drag surface** (`DRAG_SURFACE_PROPS`,
   a `data-sheet-drag-surface` attribute the scrollable-ancestor walk stops at — the inverse of
   Vaul's `data-vaul-no-drag`). TxnDetail is the one sheet whose root is its own scroller, so its
   sticky grab bar and title row would otherwise resolve the scrolled root and be declined; they
   carry the attribute plus their own `touch-action: none`. Every other sheet's chrome is a flex
   sibling of its scroll body and needs nothing.

**Trade-off accepted:** scrolling content to the top and continuing the same pull no longer flows
into a dismiss; the finger has to lift and the next pull dismisses. This is how Vaul behaves. It is
a step down from OS-native sheets — UIKit's sheet and Android's `BottomSheetBehavior` do continue a
scroll into a dismiss in one motion, because the OS has nested-scroll delegation between a scroll
view and its parent; the web platform has no such primitive, and reproducing it is exactly the
JS-drives-scrollTop race this decision retires. Not a regression against any web reference.

## Consequences

### Positive

- The hook shrinks from ~485 to ~360 lines with no momentum sim and no per-frame handoff state;
  the scroll-ownership predicates and the touch veto live in their own ~95-line
  `sheetScrollOwnership.ts` (no React, no physics).
- Scroll fighting drag, jumpy handoff, and wrong-feeling momentum are structurally impossible
  rather than tuned away — the browser owns scroll physics entirely once a gesture is `native`.
- A new scrollable region added inside any of the nine sheet consumers needs no CSS opt-in; it
  scrolls correctly by default.
- Dismiss thresholds return to their natural values (0.35 / 0.5) instead of carrying a compensation
  fudge for JS-injected velocity.

### Negative

- "Scroll to top, keep pulling, sheet dismisses in one motion" no longer works — matches Vaul, is
  a step down from OS-native sheets (see the trade-off above), and is a felt behavior change from
  the JS-momentum version some users may have gotten used to.
- A sheet whose root is its own scroller must mark its in-scroller chrome with `DRAG_SURFACE_PROPS`
  or a handle drag while scrolled silently becomes a scroll. Only TxnDetail has this shape today;
  the better long-term fix for any new sheet is the other five's DOM shape (chrome as flex siblings
  of a separate scroll body), which needs no opt-in.
- Fourteen CSS rules lost `touch-action: none` on the sheets' scroll regions (`index.css`
  `.ui-modal__body`, `.kebab-sheet-body`, `.mb-sheet-body`; `plan.css` `.pl-modal-body`,
  `.pl-addcats-list`, `.pl-autoprev`, `.pl-catmgr-table`, `.pl-nemod-list`; `accounts.css`
  `.acct-wizard`, `.pm-list`, `.pm-main`, `.acct-detail-email-body`, `.acct-detail.sheet`,
  `.imp-skipped`) — a future contributor adding a new sheet must know NOT to add it back, or the
  region silently stops scrolling. The hook's top-of-file comment states the contract; there is no
  lint for it.
- Safari/iOS touch semantics cannot be exercised in CI (jsdom and mouse-driven Playwright are both
  blind to `touch-action` and real touch/scroll interaction). Verification is vitest hook tests
  plus Chromium CDP touch harnesses in `frontend/qa/`; the operator's phone remains the final,
  irreplaceable check for this class of bug.

### Follow-ups

- Restructure TxnDetail's sheet to the other five's shape (grab bar + title row as flex siblings of
  a scrollable `.acct-detail-body`) so the opt-in becomes unnecessary and its root can carry
  `touch-action: none` like every other sheet. Not blocking — the opt-in is verified in a real
  touch pipeline (`frontend/qa/sheet-drag-native-scroll-touch.mjs`, scenario 2b).
- Close-button hit targets are inconsistent across sheets (`.mb-sheet-x`/`.kebab-sheet-x` use
  `--tap-min`; `.ui-modal__close`, `.pl-modal-x`, `.acct-detail-x` are ~24–28px). Pre-existing,
  unrelated to the gesture; noted by the design review of this change.

## Alternatives considered

- **Keep JS owning all scrolling, fix the handoff bugs in place** — rejected: the handoff class of
  bug (scroll fighting drag, jumpy transitions, wrong momentum feel) is inherent to simulating a
  scroll container in JS while a real one exists underneath; every fix so far was a new special
  case, not a structural one.
- **Adopt Vaul directly** — rejected. Vaul uses the same gesture model and the same iOS gating this
  decision reproduces, so there is no behavioral upside; adopting it would mean rewriting all nine
  consumers' DOM, animations, and body-scroll-lock (Radix Dialog + react-remove-scroll already
  handle Modal's), for a library that carries open iOS body-lock issues (#656, #641). Reproducing
  its contract in the existing hook keeps all nine consumers' handler API
  (`onPointerDown/Move/Up/Cancel`) unchanged.
- **`preventDefault()` on pointermove with `touch-action: pan-y`** — rejected: this is the mechanism
  the first native-scroll attempt (`4b2b93f91`) used and it cannot work — cancelling pointermove
  does not veto native scrolling per the pointer-events spec. This was the misdiagnosis that led to
  the JS-owns-everything design in the first place.
- **Re-evaluate drag-vs-scroll every frame instead of once per gesture** — rejected: this is what
  produced the mid-gesture handoff bugs; deciding once, at the first movement, is what makes "a
  gesture that scrolled can never dismiss" a structural guarantee instead of a race.

## Agent provenance

- **Decided by:** Claude (bug-fix task), ratified by the repo owner.
- **Lane / branch:** `feat/sheet-native-scroll` (worktree `onw-sheet-native-scroll`).
- **Alternatives weighed:** keep-and-patch the JS-owns-scrolling design; adopt Vaul wholesale;
  pointermove `preventDefault()` with `touch-action: pan-y`; per-frame drag-vs-scroll
  re-evaluation.
- **Driving artifacts:** `frontend/src/hooks/useSheetDragToDismiss.ts` (new implementation and its
  top-of-file comment) and `frontend/src/hooks/sheetScrollOwnership.ts` (the shared ownership
  predicate and the veto); `frontend/src/hooks/useHorizontalOverscrollLock.ts` (the proven
  non-passive-`touchmove`-veto mechanism this generalizes); Vaul source
  (`[data-vaul-drawer]{touch-action:none}`, `shouldDrag`, `scrollLockTimeout`); react-aria's
  `usePreventScroll` iOS rule; commit `4b2b93f91` (the first, misdiagnosed native-scroll attempt).
- **Why this way (one line):** the browser already has a correct, battle-tested scroll
  implementation directly under the sheet — the fix is to get out of its way except at the one
  moment (a boundary, in the direction of travel) where a drag needs to claim the gesture instead.
