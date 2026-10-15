import { useEffect, useLayoutEffect, useRef, useState, type RefObject } from 'react'

// Zoom for a document's pages in an app that turns the browser's own pinch-zoom off
// (index.html's viewport — owner: "seems like i can't zoom in"). The caller widens its content to
// `zoom × 100%`; the scroller around it pans with its own native scroll, both ways, and the point
// under the fingers (or the pointer) stays where it was.

const MIN_ZOOM = 1
const MAX_ZOOM = 4
const TAP_ZOOM = 2.5
const DOUBLE_TAP_MS = 300
const TAP_SLOP = 10

const clamp = (z: number) => Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, z))

/** Where a zoom step anchors: the point in unzoomed content units, and where it should end up in
 *  the scroller's view. */
interface Anchor {
  ux: number
  uy: number
  mx: number
  my: number
}

/** Pinch with two fingers, double-tap (or double-click), or pinch a trackpad (ctrl + wheel). */
export function usePinchZoom(
  contentRef: RefObject<HTMLElement | null>,
  scrollerSelector = '.ui-modal__body',
): number {
  const [zoom, setZoom] = useState(1)
  const zoomRef = useRef(1)
  const anchor = useRef<Anchor | null>(null)

  useLayoutEffect(() => {
    const scroller = contentRef.current?.closest<HTMLElement>(scrollerSelector)
    const at = anchor.current
    if (!scroller || !at) return
    scroller.scrollLeft = at.ux * zoom - at.mx
    scroller.scrollTop = at.uy * zoom - at.my
    anchor.current = null
  }, [zoom, contentRef, scrollerSelector])

  useEffect(() => {
    const content = contentRef.current
    const scroller = content?.closest<HTMLElement>(scrollerSelector)
    if (!content || !scroller) return
    const view = (x: number, y: number) => {
      const r = scroller.getBoundingClientRect()
      return { mx: x - r.left, my: y - r.top }
    }
    const zoomTo = (next: number, at: Anchor) => {
      const z = clamp(next)
      if (z === zoomRef.current) return
      anchor.current = at
      zoomRef.current = z
      setZoom(z)
    }
    const anchorAt = (x: number, y: number): Anchor => {
      const { mx, my } = view(x, y)
      const z = zoomRef.current
      return { ux: (scroller.scrollLeft + mx) / z, uy: (scroller.scrollTop + my) / z, mx, my }
    }

    let pinch: { d0: number; z0: number; ux: number; uy: number } | null = null
    let tap: { x: number; y: number; moved: boolean } | null = null
    let lastTap: { at: number; x: number; y: number } | null = null
    const span = (t: TouchList) =>
      Math.hypot(t[0]!.clientX - t[1]!.clientX, t[0]!.clientY - t[1]!.clientY)
    const mid = (t: TouchList) => ({
      x: (t[0]!.clientX + t[1]!.clientX) / 2,
      y: (t[0]!.clientY + t[1]!.clientY) / 2,
    })

    const onTouchStart = (e: TouchEvent) => {
      if (e.touches.length === 2) {
        const m = mid(e.touches)
        const start = anchorAt(m.x, m.y)
        pinch = { d0: span(e.touches), z0: zoomRef.current, ux: start.ux, uy: start.uy }
        tap = null
      } else if (e.touches.length === 1) {
        tap = { x: e.touches[0]!.clientX, y: e.touches[0]!.clientY, moved: false }
      }
    }
    const onTouchMove = (e: TouchEvent) => {
      if (pinch && e.touches.length === 2) {
        e.preventDefault() // the fingers zoom; they don't also scroll
        const m = mid(e.touches)
        const { mx, my } = view(m.x, m.y)
        zoomTo(pinch.z0 * (span(e.touches) / pinch.d0), { ux: pinch.ux, uy: pinch.uy, mx, my })
      } else if (tap && e.touches.length === 1) {
        const t = e.touches[0]!
        if (Math.hypot(t.clientX - tap.x, t.clientY - tap.y) > TAP_SLOP) tap.moved = true
      }
    }
    const onTouchEnd = (e: TouchEvent) => {
      if (e.touches.length < 2) pinch = null
      if (!tap || tap.moved || e.touches.length > 0) return
      const now = e.timeStamp
      const { x, y } = tap
      tap = null
      if (
        lastTap &&
        now - lastTap.at < DOUBLE_TAP_MS &&
        Math.hypot(x - lastTap.x, y - lastTap.y) < 30
      ) {
        e.preventDefault() // no synthetic click, no second zoom from dblclick
        lastTap = null
        zoomTo(zoomRef.current > 1 ? 1 : TAP_ZOOM, anchorAt(x, y))
      } else {
        lastTap = { at: now, x, y }
      }
    }
    const onDoubleClick = (e: MouseEvent) =>
      zoomTo(zoomRef.current > 1 ? 1 : TAP_ZOOM, anchorAt(e.clientX, e.clientY))
    // A trackpad pinch arrives as a wheel with ctrlKey; a plain wheel stays the scroller's.
    const onWheel = (e: WheelEvent) => {
      if (!e.ctrlKey) return
      e.preventDefault()
      zoomTo(zoomRef.current * Math.exp(-e.deltaY * 0.01), anchorAt(e.clientX, e.clientY))
    }

    content.addEventListener('touchstart', onTouchStart, { passive: true })
    content.addEventListener('touchmove', onTouchMove, { passive: false })
    content.addEventListener('touchend', onTouchEnd, { passive: false })
    content.addEventListener('touchcancel', onTouchEnd, { passive: false })
    content.addEventListener('dblclick', onDoubleClick)
    content.addEventListener('wheel', onWheel, { passive: false })
    return () => {
      content.removeEventListener('touchstart', onTouchStart)
      content.removeEventListener('touchmove', onTouchMove)
      content.removeEventListener('touchend', onTouchEnd)
      content.removeEventListener('touchcancel', onTouchEnd)
      content.removeEventListener('dblclick', onDoubleClick)
      content.removeEventListener('wheel', onWheel)
    }
  }, [contentRef, scrollerSelector])

  return zoom
}
