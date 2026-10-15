// useHorizontalOverscrollLock — WebKit doesn't honor `overscroll-behavior: none` for
// suppressing the native elastic rubber-band bounce on a non-body scrollable element (only for
// scroll CHAINING to an ancestor); iOS Safari still shows the bounce regardless
// (webkit.org/b/176454). The reliable, cross-engine fix is the same "JS owns the boundary"
// approach useSheetDragToDismiss already uses for vertical: once a touch gesture is already at
// a scroll edge, preventDefault() on further movement PAST that edge so the browser's native
// scroll — and its elastic bounce — never gets a chance to run past bounds. Native momentum
// scrolling inside the bounds is untouched; this only ever intervenes at the edge.
import { useEffect, type RefObject } from 'react'

export function useHorizontalOverscrollLock(ref: RefObject<HTMLElement | null>): void {
  useEffect(() => {
    const el = ref.current
    if (!el) return
    let lastX = 0

    const onTouchStart = (e: TouchEvent) => {
      if (e.touches.length !== 1) return
      lastX = e.touches[0]!.clientX
    }
    const onTouchMove = (e: TouchEvent) => {
      if (e.touches.length !== 1) return
      const x = e.touches[0]!.clientX
      const dx = x - lastX
      lastX = x
      const maxScroll = el.scrollWidth - el.clientWidth
      // dx > 0 = finger moving right = revealing content to the left = scrollLeft decreasing.
      const atStart = el.scrollLeft <= 0
      const atEnd = el.scrollLeft >= maxScroll
      if ((atStart && dx > 0) || (atEnd && dx < 0)) e.preventDefault()
    }

    el.addEventListener('touchstart', onTouchStart, { passive: true })
    // Non-passive: this listener's whole job is calling preventDefault() at the scroll edge.
    el.addEventListener('touchmove', onTouchMove, { passive: false })
    return () => {
      el.removeEventListener('touchstart', onTouchStart)
      el.removeEventListener('touchmove', onTouchMove)
    }
  }, [ref])
}
