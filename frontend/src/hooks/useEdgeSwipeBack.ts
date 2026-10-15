// useEdgeSwipeBack — iOS's "swipe from the left edge to go back", for an in-app pushed view
// (a Documents item over its list). The installed (standalone) app has no browser back
// gesture at all (owner report: "can't go back by swiping back"); in a browser tab the
// browser's own edge swipe owns that edge, so this arms only where that is missing.
//
// Touch events, not pointer events: once a sideways pull is decided, a non-passive touchmove
// is the one thing that can keep iOS from also scrolling. A touch that starts at the edge is
// kept from the bottom sheet's own drag-to-dismiss (it would read a sideways move over
// non-scrolling chrome as a drag), so the two gestures never share one finger.

import { useEffect, useRef, type RefObject } from 'react'

const EDGE_PX = 24
const DECIDE_PX = 10
const COMMIT_FRACTION = 0.3
const COMMIT_VELOCITY = 0.45 // px per ms
const SETTLE_MS = 180

export function isStandaloneApp(): boolean {
  const nav = navigator as Navigator & { standalone?: boolean }
  return nav.standalone === true || !!window.matchMedia?.('(display-mode: standalone)').matches
}

export function useEdgeSwipeBack(
  ref: RefObject<HTMLElement | null>,
  onBack: () => void,
  enabled: boolean,
): void {
  const back = useRef(onBack)
  useEffect(() => {
    back.current = onBack
  })
  useEffect(() => {
    const el = ref.current
    if (!el || !enabled) return
    let start: { x: number; y: number; t: number } | null = null
    let swiping = false
    let dx = 0
    const atEdge = (x: number) => x - el.getBoundingClientRect().left <= EDGE_PX
    const reset = () => {
      el.style.transition = ''
      el.style.transform = ''
      delete el.dataset.swiping
    }
    const settle = (to: string, then?: () => void) => {
      el.style.transition = `transform ${SETTLE_MS}ms ease-out`
      el.style.transform = to
      window.setTimeout(() => {
        if (!then) return reset()
        // Leave the page where it slid to and go back FIRST: resetting it here painted it in
        // place for a frame before the list took over (owner: "a flash of it showing back").
        // Only a page still on screen two frames later — nothing replaced it — is put back.
        then()
        requestAnimationFrame(() => requestAnimationFrame(() => el.isConnected && reset()))
      }, SETTLE_MS)
    }
    const onPointerDown = (e: PointerEvent) => {
      if (e.pointerType === 'touch' && atEdge(e.clientX)) e.stopPropagation()
    }
    const onStart = (e: TouchEvent) => {
      const touch = e.touches[0]
      start =
        e.touches.length === 1 && touch && atEdge(touch.clientX)
          ? { x: touch.clientX, y: touch.clientY, t: e.timeStamp }
          : null
      swiping = false
      dx = 0
    }
    const onMove = (e: TouchEvent) => {
      const touch = e.touches[0]
      if (!start || !touch) return
      dx = touch.clientX - start.x
      const dy = touch.clientY - start.y
      if (!swiping) {
        if (Math.abs(dy) > DECIDE_PX && Math.abs(dy) >= Math.abs(dx)) {
          start = null // a scroll, not a swipe
          return
        }
        if (dx <= DECIDE_PX || dx <= Math.abs(dy)) return
        swiping = true
        el.dataset.swiping = '' // styling hook: the page's edge shadow while it slides
        el.style.transition = 'none'
      }
      e.preventDefault()
      e.stopPropagation()
      el.style.transform = `translateX(${Math.max(0, dx)}px)`
    }
    const onEnd = (e: TouchEvent) => {
      if (!start || !swiping) {
        start = null
        return
      }
      const width = el.getBoundingClientRect().width
      const velocity = dx / Math.max(1, e.timeStamp - start.t)
      start = null
      swiping = false
      if (dx > width * COMMIT_FRACTION || velocity > COMMIT_VELOCITY) {
        settle(`translateX(${width}px)`, () => back.current())
      } else {
        settle('translateX(0px)')
      }
    }
    el.addEventListener('pointerdown', onPointerDown)
    el.addEventListener('touchstart', onStart, { passive: true })
    el.addEventListener('touchmove', onMove, { passive: false })
    el.addEventListener('touchend', onEnd)
    el.addEventListener('touchcancel', onEnd)
    return () => {
      el.removeEventListener('pointerdown', onPointerDown)
      el.removeEventListener('touchstart', onStart)
      el.removeEventListener('touchmove', onMove)
      el.removeEventListener('touchend', onEnd)
      el.removeEventListener('touchcancel', onEnd)
      el.style.transition = ''
      el.style.transform = ''
      delete el.dataset.swiping
    }
  }, [ref, enabled])
}
