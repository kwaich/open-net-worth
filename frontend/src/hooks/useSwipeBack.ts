// useSwipeBack — a left-to-right swipe over a surface calls `onBack`, the touch equivalent of
// a "‹ Back" chevron. Pure gesture recognition, no DOM mutation: it never touches the
// element's transform/style, so there is nothing to leave stuck if a gesture is interrupted,
// and — unlike an approach that only decides on release — it fires as soon as the swipe is
// unambiguous, so it never depends on actually receiving a clean pointerup/pointercancel (a
// real hazard on a touchscreen when iOS's own edge swipe-back gesture competes for the same
// touch and can swallow the release event entirely).
//
// Decided ONCE, at the gesture's first real movement (mirrors useSheetDragToDismiss's own
// drag-vs-scroll philosophy): a rightward, horizontal-dominant pull commits to the swipe;
// anything else — including any vertical scroll — is left entirely to the browser/content, so
// a normal scroll can never be hijacked mid-gesture.
//
// stopPropagation() once committed to a swipe: Settings' Modal has its OWN whole-card vertical
// drag-to-dismiss (`mobileDockedSheet`), and these handlers sit on a DESCENDANT of that card, so
// every pointer event here also bubbles up to it. That hook treats ANY movement as "drag the
// sheet" whenever it can't find scrollable content under the finger (`findScrollableAncestor`
// requires actual overflow — a short section like Appearance has none) — so a mostly-horizontal
// swipe with the slightest real-finger vertical wobble was ALSO nudging the sheet down (owner
// report 2026-09-04). Once this hook claims a gesture as a rightward swipe, the card-level hook
// must never see any of its MOVES. It does still get the pointerdown (it fires before anything
// is decided) and, on purpose, the release — see `end`.
import { useEffect, useMemo, useRef } from 'react'
import type { PointerEvent as ReactPointerEvent } from 'react'

const CATCH_THRESHOLD_PX = 4
const COMMIT_DISTANCE_PX = 80
const COMMIT_VELOCITY_PX_MS = 0.5

export interface SwipeBackHandlers {
  onPointerDown: (event: ReactPointerEvent) => void
  onPointerMove: (event: ReactPointerEvent) => void
  onPointerUp: (event: ReactPointerEvent) => void
  onPointerCancel: (event: ReactPointerEvent) => void
}

type GesturePhase = 'pending' | 'swipe' | 'native' | 'fired'

interface SwipeState {
  pointerId: number
  startX: number
  startY: number
  startT: number
  phase: GesturePhase
}

/** Fires `onBack` once a rightward swipe reads as deliberate — past `COMMIT_DISTANCE_PX`, or a
 * fast flick under it. `enabled` gates the whole gesture (e.g. only while a mobile drill-down
 * is actually open). */
export function useSwipeBack(onBack: () => void, enabled = true): SwipeBackHandlers {
  const onBackRef = useRef(onBack)
  const enabledRef = useRef(enabled)
  useEffect(() => {
    onBackRef.current = onBack
    enabledRef.current = enabled
  })

  const state = useRef<SwipeState | null>(null)

  return useMemo(() => {
    const onPointerDown = (event: ReactPointerEvent) => {
      if (!enabledRef.current) return
      if (event.pointerType === 'mouse' && event.button !== 0) return
      state.current = {
        pointerId: event.pointerId,
        startX: event.clientX,
        startY: event.clientY,
        startT: event.timeStamp,
        phase: 'pending',
      }
    }

    const onPointerMove = (event: ReactPointerEvent) => {
      const s = state.current
      // 'native' is the only phase this hook may ever let bubble freely — everything else
      // (still deciding, decided swipe, already fired) is OURS for the rest of the gesture.
      if (!s || s.pointerId !== event.pointerId || s.phase === 'native') return
      const dx = event.clientX - s.startX
      const dy = event.clientY - s.startY
      if (s.phase === 'pending') {
        if (Math.max(Math.abs(dx), Math.abs(dy)) < CATCH_THRESHOLD_PX) return
        s.phase = dx > 0 && Math.abs(dx) > Math.abs(dy) ? 'swipe' : 'native'
        if (s.phase !== 'swipe') return
      }
      // Claimed: stop this event from reaching the card-level drag-to-dismiss hook further up
      // the tree. Unconditional from here — including every TRAILING move after `onBack` has
      // already fired, while the finger is still on the glass. Without this, only the
      // events up to commit were ever claimed; the rest of the physical gesture (still
      // generating pointermoves right up to lift-off) leaked straight through and nudged the
      // sheet down exactly as if this hook didn't exist for that tail end (owner report
      // 2026-09-04, "swiping right also pulls the sheet down").
      event.stopPropagation()
      if (s.phase === 'fired') return
      const dt = event.timeStamp - s.startT
      const velocity = dt > 0 ? dx / dt : 0
      if (dx > COMMIT_DISTANCE_PX || velocity > COMMIT_VELOCITY_PX_MS) {
        s.phase = 'fired'
        onBackRef.current()
      }
    }

    const end = (event: ReactPointerEvent) => {
      const s = state.current
      if (!s || s.pointerId !== event.pointerId) return
      // The release is deliberately NOT stopped, whatever the phase. The card-level hook saw
      // this pointer's pointerdown (only moves are hidden from it), so it is tracking the
      // pointer and needs its pointerup/pointercancel to let go. Stopping it here left that
      // hook holding a pointer that never ended — and since every touch is a NEW pointerId,
      // every later touch on the card was refused as "a second concurrent pointer", which is
      // why drag-to-dismiss died after a swipe-back (owner report 2026-09-05). The release
      // is harmless there: a pointer it never saw move settles as a tap.
      state.current = null
    }

    return {
      onPointerDown,
      onPointerMove,
      onPointerUp: end,
      onPointerCancel: end,
    }
  }, [])
}
