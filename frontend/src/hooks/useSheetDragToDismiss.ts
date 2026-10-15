// useSheetDragToDismiss — the one gesture behind every mobile bottom sheet (Modal's phone mode,
// .mb-sheet, .kebab-sheet, PlanningModal, TxnDetail). Owns the live pointer-drag →
// transform/opacity math; each pattern keeps its own JSX, CSS and close plumbing and only wires
// this in.
//
// Imperative by design: the transform and scrim opacity are written straight to the DOM via refs
// on every pointermove, never through React state. A per-frame setState here would mean a full
// render for every pixel of finger movement — exactly the "no lag" 1:1 tracking the gesture
// needs is what a render cycle would jeopardize.
//
// NATIVE SCROLL; THE SHEET CLAIMS A TOUCH ONLY AT A SCROLL BOUNDARY. This is the contract Vaul
// (and react-aria's iOS scroll lock underneath it) ship, reproduced here on purpose so a later
// swap to that library would be mechanical — see docs/adr/2026-09-03-sheet-gesture-native-scroll.md
// for why the previous "JS owns all scrolling" design was retired:
//
//   1. Content scrolls natively. `touch-action: none` lives on the sheet ROOT (and handles)
//      only — per the pointer-events spec, that value never reaches into a descendant scroll
//      container, so lists inside the sheet keep the browser's own momentum, deceleration and
//      rubber-band. A new scrollable region inside a sheet needs no CSS opt-in at all.
//   2. Whether a gesture is a DRAG or a SCROLL is decided ONCE, at its first real movement:
//      it's a drag when there is nothing scrollable under the finger, or the scrollable is at
//      its top and the finger moves down (not sideways — a wide table pans). Anything else is a
//      scroll for the rest of that gesture — a gesture that scrolled can never turn into a
//      dismiss, so the "scrolled back to the top and it dismissed on me" class of bug is
//      structurally impossible rather than tuned away. The cost is the one thing native iOS
//      sheets do that web ones don't: scroll-to-top-and-keep-pulling in a single motion stops
//      at the top; the NEXT pull dismisses.
//   3. The browser must not start a native scroll (or, on iOS, an elastic bounce — which also
//      cancels our pointer stream) for a touch WE are going to drag. The only thing that can
//      veto native scrolling per-gesture is `preventDefault()` on a cancelable `touchmove`,
//      which requires a NON-passive listener attached directly to the element — React's own
//      `onTouchMove` is registered passive and cannot do it, and `preventDefault()` on a
//      pointermove never could (that was the actual defect behind the first native-scroll
//      attempt here, misread at the time as an unwinnable race). `bindTouchVeto`
//      (sheetScrollOwnership.ts) is that listener: it cancels a touchmove exactly when the
//      nearest scrollable is at the boundary in the direction of travel (or there is none) —
//      `sheetOwnsMovement`, the same rule the drag decision uses.
//   4. Chrome that lives INSIDE the scroll container (TxnDetail's sticky grab bar and title
//      row — its sheet root is its own scroller) opts in with `DRAG_SURFACE_PROPS`: a touch
//      that starts there is the sheet's whatever the scroll position, exactly like a handle
//      that sits outside the scroller. Everywhere else the DOM shape already says it.

import { useEffect, useMemo, useRef } from 'react'
import type { PointerEvent as ReactPointerEvent, RefObject } from 'react'
import {
  RUBBER_BAND_MAX_PX,
  VELOCITY_WINDOW_MS,
  computeVelocity,
  dragOffset,
  dragProgress,
  scrimOpacity,
  shouldCommitDismiss,
  type PointerSample,
} from './sheetDragPhysics'
import {
  bindTouchVeto,
  findScrollableAncestor,
  ownsItsGesture,
  sheetOwnsMovement,
} from './sheetScrollOwnership'

// Consumers wire the whole gesture from this one module.
export { DRAG_IGNORE_PROPS, DRAG_SURFACE_PROPS } from './sheetScrollOwnership'

// Reuses the exit curve `.mb-sheet-out`/`.mb-sheet-scrim-out` already animate with, so a
// released-and-committed drag feels like the same motion as a programmatic close.
const EXIT_EASE = 'cubic-bezier(0.32, 0.72, 0, 1)'
const DISMISS_COMMIT_MS = 200
const SPRING_BACK_MS = 220
// Below this a pointerdown's own hand-tremor jitter would otherwise register as a drag and
// steal the tap from whatever button sits under it (or the click on a list row).
const CATCH_THRESHOLD_PX = 4
// A drag is refused this soon after a gesture was declined in favour of scrolling — Vaul's
// `scrollLockTimeout`, same default. Guards a second finger-down that lands while the content
// is still settling from the previous scroll, so it reads as "stop the scroll", not "dismiss".
export const SCROLL_LOCK_TIMEOUT_MS = 100

export interface UseSheetDragToDismissOptions<
  TSheet extends HTMLElement,
  TOverlay extends HTMLElement,
> {
  sheetRef: RefObject<TSheet | null>
  /** The scrim/backdrop, faded in lockstep with drag progress. Omit for a sheet with none. */
  overlayRef?: RefObject<TOverlay | null>
  /** Fires once a committed release's off-screen animation finishes. The ONLY place that may
   * flip the sheet's own open/closed state — this hook never touches it directly, so each
   * pattern's own Radix/portal close plumbing stays the single source of truth. */
  onDismiss: () => void
  /** false disables the whole gesture (e.g. desktop Modal). Default true. */
  enabled?: boolean
  /** Vetoes arming the gesture for content that isn't a draggable sheet — e.g. Modal hosting
   * the full-screen command palette. Checked once, right before the drag would arm. */
  canStart?: () => boolean
}

export interface SheetDragHandlers {
  onPointerDown: (event: ReactPointerEvent) => void
  onPointerMove: (event: ReactPointerEvent) => void
  onPointerUp: (event: ReactPointerEvent) => void
  onPointerCancel: (event: ReactPointerEvent) => void
}

/** `pending` until the finger has moved CATCH_THRESHOLD_PX; then decided once, for good —
 * `native` is the browser's gesture (a scroll, or content that vetoed dragging), not ours. */
type GesturePhase = 'pending' | 'drag' | 'native'

interface DragState {
  pointerId: number
  startX: number
  startY: number
  scrollEl: Element | null
  phase: GesturePhase
  /** Sheet offset over time, populated only while `phase === 'drag'` — release velocity is how
   * fast the SHEET was moving. */
  samples: PointerSample[]
}

function prefersReducedMotion(): boolean {
  return window.matchMedia('(prefers-reduced-motion: reduce)').matches
}

function trimSamples(samples: PointerSample[], nowT: number) {
  const cutoff = nowT - VELOCITY_WINDOW_MS * 2
  while (samples.length > 1 && samples[0]!.t < cutoff) samples.shift()
}

function applyLiveDrag(sheet: HTMLElement, overlay: HTMLElement | null, offset: number) {
  sheet.style.transform = `translateY(${dragOffset(offset, RUBBER_BAND_MAX_PX)}px)`
  if (!overlay) return
  // `--sheet-scrim-alpha`, never `.style.opacity`: for Modal, the overlay is an ANCESTOR of
  // the sheet's own content (Dialog.Content is a DOM child of Dialog.Overlay) — writing
  // `opacity` there would fade the card's content along with the backdrop. `scrimOpacity`'s
  // 1→floor range is a MULTIPLIER on the scrim's own rest alpha (same range `opacity` used to
  // take) — each scrim's own `background` in index.css does the multiplication, feeding only
  // its `background-color` alpha, not its compositing. No-op for `.mb-sheet-scrim`/
  // `.kebab-sheet-scrim`, which are already siblings of their sheet, not ancestors.
  overlay.style.setProperty(
    '--sheet-scrim-alpha',
    String(scrimOpacity(dragProgress(offset, sheet.offsetHeight))),
  )
}

/** Shared tail for both release outcomes: animate transform+scrim-alpha to their target (or,
 * under reduced motion, skip straight there) and call `done` once settled. */
function releaseTo(
  sheet: HTMLElement,
  overlay: HTMLElement | null,
  target: { transform: string; scrimAlpha: string; durationMs: number },
  done: () => void,
) {
  if (prefersReducedMotion()) {
    done()
    return
  }
  sheet.style.transition = `transform ${target.durationMs}ms ${EXIT_EASE}`
  sheet.style.transform = target.transform
  if (overlay) {
    // `--sheet-scrim-alpha` is `@property`-registered (index.css) specifically so this
    // transition interpolates smoothly frame-by-frame, the same as the `opacity` transition
    // it replaces — an unregistered custom property would otherwise jump discretely.
    overlay.style.transition = `--sheet-scrim-alpha ${target.durationMs}ms ${EXIT_EASE}`
    overlay.style.setProperty('--sheet-scrim-alpha', target.scrimAlpha)
  }
  window.setTimeout(done, target.durationMs)
}

// `animation` is deliberately NEVER restored here (or anywhere on the spring-back path) once
// a drag has neutralized it (see the arm branch below) — only `transition`/`transform`/
// `opacity` return to CSS-driven. A CSS animation restarts from its `from` keyframe whenever
// its used value transitions out of `none`, even back to the exact name it already had — there
// is no "resume where it left off" — so clearing `.mb-sheet`'s override while `data-state` is
// still 'open' (a spring-back, not a real close) would replay its entrance keyframe: a visible
// flash off-screen and back. Leaving it neutralized for the rest of this mount is a one-time,
// deliberate trade — a later NON-drag close (tap ✕ / Escape / backdrop) on a sheet that has
// already been dragged loses its own CSS exit animation — in exchange for the far more common
// spring-back path never glitching.
function clearInlineStyles(sheet: HTMLElement, overlay: HTMLElement | null) {
  sheet.style.transition = ''
  sheet.style.transform = ''
  if (overlay) {
    overlay.style.transition = ''
    overlay.style.removeProperty('--sheet-scrim-alpha')
  }
}

/** The release decision + its animation, factored out of the hook body so the pointerup
 * handler stays a plain "resolve the state, delegate" shell. */
function settleRelease(
  sheet: HTMLElement,
  overlay: HTMLElement | null,
  offset: number,
  velocity: number,
  cancelled: boolean,
  onDismiss: () => void,
) {
  const commit =
    !cancelled &&
    shouldCommitDismiss({ offsetY: offset, sheetHeight: sheet.offsetHeight, velocity })
  if (commit) {
    releaseTo(
      sheet,
      overlay,
      { transform: 'translateY(100%)', scrimAlpha: '0', durationMs: DISMISS_COMMIT_MS },
      onDismiss,
    )
  } else {
    releaseTo(
      sheet,
      overlay,
      { transform: 'translateY(0)', scrimAlpha: '1', durationMs: SPRING_BACK_MS },
      () => clearInlineStyles(sheet, overlay),
    )
  }
}

export function useSheetDragToDismiss<
  TSheet extends HTMLElement = HTMLElement,
  TOverlay extends HTMLElement = HTMLElement,
>({
  sheetRef,
  overlayRef,
  onDismiss,
  enabled = true,
  canStart,
}: UseSheetDragToDismissOptions<TSheet, TOverlay>): SheetDragHandlers {
  const onDismissRef = useRef(onDismiss)
  const canStartRef = useRef(canStart)
  const enabledRef = useRef(enabled)
  useEffect(() => {
    onDismissRef.current = onDismiss
    canStartRef.current = canStart
    enabledRef.current = enabled
  })

  const dragState = useRef<DragState | null>(null)
  const lastScrollDeclineAt = useRef(-Infinity)
  const touchVeto = useRef<{ root: HTMLElement; unbind: () => void } | null>(null)
  useEffect(() => () => touchVeto.current?.unbind(), [])

  // Stable across renders (only re-created if the ref objects themselves change, which — being
  // useRef-produced — they never do): every changing input above is read through a ref instead,
  // so the pointer handlers' identity never churns.
  return useMemo(() => {
    const onPointerDown = (event: ReactPointerEvent) => {
      if (!enabledRef.current) return
      if (event.pointerType === 'mouse' && event.button !== 0) return
      // React events bubble through portals, so a surface portaled OUT of this sheet — a Modal
      // opened from it, like the transaction drawer's receipt viewer — lands here too. Its
      // gestures are its own: claiming one stole the pointer capture from it and slid THIS
      // sheet instead (owner report 2026-09-26). Only a touch that starts in the sheet's DOM.
      if (!event.currentTarget.contains(event.target as Node)) return
      // A second concurrent pointer (multi-touch) must never steal ownership from a DRAG
      // already in progress — see the identical hazard note in finishDrag below. Only a drag,
      // though: a pointer still undecided (or the browser's) whose release never reached us
      // — a descendant hook claimed the gesture, or iOS's own edge gesture swallowed the
      // pointerup — is let go of here, or every later touch (each a NEW pointerId) would be
      // refused as "concurrent" and the sheet could never be dragged again until remount.
      const active = dragState.current
      if (active && active.phase === 'drag' && active.pointerId !== event.pointerId) return
      const sheet = sheetRef.current
      if (!sheet) return
      // A region that owns its own gestures (DRAG_IGNORE_PROPS — the fullscreen chart's pane)
      // is left alone entirely: nothing pending, so no later move can turn into a drag.
      if (ownsItsGesture(event.target as Element, sheet)) return
      // pointerdown is dispatched before the touchstart of the same contact, so binding here
      // is in time for this very gesture. Re-bound only if the handlers moved to a new element.
      const root = event.currentTarget as HTMLElement
      if (touchVeto.current?.root !== root) {
        touchVeto.current?.unbind()
        const dragging = () => dragState.current?.phase === 'drag'
        touchVeto.current = { root, unbind: bindTouchVeto(root, dragging) }
      }
      dragState.current = {
        pointerId: event.pointerId,
        startX: event.clientX,
        startY: event.clientY,
        scrollEl: findScrollableAncestor(event.target as Element, sheet),
        phase: 'pending',
        samples: [],
      }
    }

    /** Decides a pending gesture at its first real movement. Returns the phase it settled on.
     * With nothing scrollable under the finger the sheet owns every direction (an upward pull
     * is the damped rubber-band); over scrollable content only a downward pull from the top is
     * a drag, and only once the scroll-lock window since the last declined gesture has passed.
     * Declining records the moment, which is what opens that window for the next gesture. */
    const decide = (state: DragState, dx: number, dy: number, now: number): GesturePhase => {
      if (canStartRef.current && !canStartRef.current()) return 'native'
      if (!state.scrollEl) return 'drag'
      const lockedOut = now - lastScrollDeclineAt.current < SCROLL_LOCK_TIMEOUT_MS
      const sideways = Math.abs(dx) > Math.abs(dy)
      if (dy > 0 && !lockedOut && sheetOwnsMovement(state.scrollEl, dy, sideways)) return 'drag'
      lastScrollDeclineAt.current = now
      return 'native'
    }

    const arm = (event: ReactPointerEvent, sheet: HTMLElement) => {
      // MUST capture on `event.currentTarget` (wherever the caller spread these handlers),
      // never on `sheet` specifically — per spec, once `setPointerCapture` is called,
      // subsequent events for the pointer fire with `target` retargeted to the capturing
      // element. Capturing on an ancestor different from the listener's own element takes
      // later events off that element's dispatch path entirely (confirmed live: exactly one
      // pointermove arrived before the gesture went dead, on a real touch device, before this
      // was corrected). Optional call: some embedded webviews (and jsdom) don't implement
      // pointer capture — losing it only means a very fast drag could slip off the element
      // without this safety net; the gesture still works off plain bubbling for a slower one.
      event.currentTarget.setPointerCapture?.(event.pointerId)
      sheet.style.transition = 'none'
      // Neutralizes a `forwards`-filled CSS entrance/exit animation (`.mb-sheet`'s) that would
      // otherwise keep winning the cascade for transform/opacity over these per-pixel inline
      // writes even once "finished" (a held animation, not merely a past one).
      sheet.style.animation = 'none'
      if (overlayRef?.current) {
        overlayRef.current.style.transition = 'none'
        overlayRef.current.style.animation = 'none'
      }
    }

    const onPointerMove = (event: ReactPointerEvent) => {
      const state = dragState.current
      const sheet = sheetRef.current
      if (!state || !sheet || state.pointerId !== event.pointerId) return
      if (state.phase === 'native') return
      const dy = event.clientY - state.startY
      if (state.phase === 'pending') {
        const dx = event.clientX - state.startX
        // Over a scroller a sideways pan is decided (native) as soon as it clears the threshold
        // on its own axis, while the browser is already scrolling it; over plain chrome only
        // vertical travel counts, so a wobble on the handle never arms a drag by itself.
        const caught =
          Math.abs(dy) >= CATCH_THRESHOLD_PX ||
          (state.scrollEl !== null && Math.abs(dx) >= CATCH_THRESHOLD_PX)
        if (!caught) return
        state.phase = decide(state, dx, dy, event.timeStamp)
        if (state.phase !== 'drag') return
        arm(event, sheet)
      }
      state.samples.push({ t: event.timeStamp, y: dy })
      trimSamples(state.samples, event.timeStamp)
      applyLiveDrag(sheet, overlayRef?.current ?? null, dy)
    }

    const finishDrag = (event: ReactPointerEvent, cancelled: boolean) => {
      const state = dragState.current
      const sheet = sheetRef.current
      // Only the pointer actually being tracked may resolve (and clear) dragState — a
      // mismatched pointerup/pointercancel (e.g. a second finger lifting while the first is
      // still mid-drag) must be a true no-op here, not clear the in-progress gesture out from
      // under the pointer that owns it (see onPointerDown's comment on the same hazard).
      if (!state || !sheet || state.pointerId !== event.pointerId) return
      dragState.current = null
      // A tap (never moved) or a scroll (the browser's, not ours) — nothing to settle.
      if (state.phase !== 'drag') return
      // Snapshot NOW, not when the exit timer eventually fires (releaseTo's setTimeout, up to
      // DISMISS_COMMIT_MS later): onDismissRef.current gets reassigned by every re-render, so a
      // state change in that window (e.g. TxnDetail's mode flipping back to 'view' on a txn
      // switch while the exit animation is still running) would otherwise silently redirect a
      // drag that was already committed onto whatever the LATEST render's onDismiss closure
      // does instead of what was actually intended at the moment it committed.
      const dismiss = onDismissRef.current
      const offset = state.samples[state.samples.length - 1]?.y ?? 0
      settleRelease(
        sheet,
        overlayRef?.current ?? null,
        offset,
        computeVelocity(state.samples),
        cancelled,
        dismiss,
      )
    }

    return {
      onPointerDown,
      onPointerMove,
      onPointerUp: (event: ReactPointerEvent) => finishDrag(event, false),
      onPointerCancel: (event: ReactPointerEvent) => finishDrag(event, true),
    }
  }, [sheetRef, overlayRef])
}
