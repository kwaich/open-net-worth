import { useEffect, useRef, useState } from 'react'
import { DRAG_IGNORE_PROPS } from '../../hooks/useSheetDragToDismiss'
import './ZoomImage.css'

// A photo that zooms in place, like a phone's own photo viewer — the app has the browser's
// pinch-zoom off (index.html's viewport; owner: "seems like i can't zoom in"). Pinch, double-tap
// (or double-click) or a trackpad pinch zooms around the fingers or the pointer; while zoomed, a
// drag (or a scroll) pans. At 1× it fits its frame whole. The frame owns its touches
// (DRAG_IGNORE_PROPS), so a pinch never slides a phone sheet.

const MAX_ZOOM = 5
const TAP_ZOOM = 2.5
const DOUBLE_TAP_MS = 300
const TAP_SLOP = 10

interface View {
  z: number
  x: number
  y: number
}

const AT_REST: View = { z: 1, x: 0, y: 0 }

/** Zoom within 1–5×, and never pan the photo's edge past the frame's. */
function contain(v: View, w: number, h: number): View {
  const z = Math.min(MAX_ZOOM, Math.max(1, v.z))
  if (z === 1) return AT_REST
  return {
    z,
    x: Math.min(0, Math.max(w - w * z, v.x)),
    y: Math.min(0, Math.max(h - h * z, v.y)),
  }
}

export function ZoomImage({
  src,
  alt,
  className,
}: {
  src: string
  alt: string
  className?: string
}) {
  const frameRef = useRef<HTMLDivElement>(null)
  const [view, setView] = useState<View>(AT_REST)
  const viewRef = useRef<View>(AT_REST)

  useEffect(() => {
    const frame = frameRef.current
    if (!frame) return
    const show = (next: View) => {
      const v = contain(next, frame.clientWidth, frame.clientHeight)
      viewRef.current = v
      setView(v)
    }
    const local = (x: number, y: number) => {
      const r = frame.getBoundingClientRect()
      return { x: x - r.left, y: y - r.top }
    }
    // The point of the photo under (px, py) in `from` stays under (qx, qy) at zoom `z`.
    const zoomKeeping = (from: View, z: number, px: number, py: number, qx = px, qy = py) => {
      const ix = (px - from.x) / from.z
      const iy = (py - from.y) / from.z
      show({ z, x: qx - ix * z, y: qy - iy * z })
    }
    const toggleAt = (x: number, y: number) => {
      const p = local(x, y)
      zoomKeeping(viewRef.current, viewRef.current.z > 1 ? 1 : TAP_ZOOM, p.x, p.y)
    }

    let pinch: { d0: number; from: View; px: number; py: number } | null = null
    let pan: { x0: number; y0: number; from: View } | null = null
    let tap: { x: number; y: number; moved: boolean } | null = null
    let lastTap: { at: number; x: number; y: number } | null = null
    const span = (t: TouchList) =>
      Math.hypot(t[0]!.clientX - t[1]!.clientX, t[0]!.clientY - t[1]!.clientY)
    const mid = (t: TouchList) =>
      local((t[0]!.clientX + t[1]!.clientX) / 2, (t[0]!.clientY + t[1]!.clientY) / 2)

    const onTouchStart = (e: TouchEvent) => {
      if (e.touches.length === 2) {
        const m = mid(e.touches)
        pinch = { d0: span(e.touches), from: viewRef.current, px: m.x, py: m.y }
        pan = null
        tap = null
      } else if (e.touches.length === 1) {
        const t = e.touches[0]!
        pan = { x0: t.clientX, y0: t.clientY, from: viewRef.current }
        tap = { x: t.clientX, y: t.clientY, moved: false }
      }
    }
    const onTouchMove = (e: TouchEvent) => {
      if (pinch && e.touches.length === 2) {
        e.preventDefault()
        const m = mid(e.touches)
        const z = pinch.from.z * (span(e.touches) / pinch.d0)
        zoomKeeping(pinch.from, z, pinch.px, pinch.py, m.x, m.y)
        return
      }
      const t = e.touches[0]
      if (!t || !pan) return
      if (tap && Math.hypot(t.clientX - tap.x, t.clientY - tap.y) > TAP_SLOP) tap.moved = true
      if (pan.from.z > 1) {
        e.preventDefault()
        show({
          ...pan.from,
          x: pan.from.x + t.clientX - pan.x0,
          y: pan.from.y + t.clientY - pan.y0,
        })
      }
    }
    const onTouchEnd = (e: TouchEvent) => {
      if (e.touches.length < 2) pinch = null
      if (e.touches.length === 0) pan = null
      if (!tap || tap.moved || e.touches.length > 0) return
      const { x, y } = tap
      tap = null
      if (
        lastTap &&
        e.timeStamp - lastTap.at < DOUBLE_TAP_MS &&
        Math.hypot(x - lastTap.x, y - lastTap.y) < 30
      ) {
        e.preventDefault() // no synthetic click, and no second toggle from dblclick
        lastTap = null
        toggleAt(x, y)
      } else {
        lastTap = { at: e.timeStamp, x, y }
      }
    }
    // A mouse drags to pan (touch pans above); a double-click toggles.
    let drag: { x0: number; y0: number; from: View } | null = null
    const onPointerDown = (e: PointerEvent) => {
      if (e.pointerType !== 'mouse' || viewRef.current.z === 1) return
      drag = { x0: e.clientX, y0: e.clientY, from: viewRef.current }
      frame.setPointerCapture?.(e.pointerId)
    }
    const onPointerMove = (e: PointerEvent) => {
      if (!drag) return
      show({
        ...drag.from,
        x: drag.from.x + e.clientX - drag.x0,
        y: drag.from.y + e.clientY - drag.y0,
      })
    }
    const onPointerUp = () => {
      drag = null
    }
    const onDoubleClick = (e: MouseEvent) => toggleAt(e.clientX, e.clientY)
    // A trackpad pinch is a wheel with ctrlKey; a plain wheel pans once zoomed.
    const onWheel = (e: WheelEvent) => {
      const v = viewRef.current
      if (e.ctrlKey) {
        e.preventDefault()
        const p = local(e.clientX, e.clientY)
        zoomKeeping(v, v.z * Math.exp(-e.deltaY * 0.01), p.x, p.y)
      } else if (v.z > 1) {
        e.preventDefault()
        show({ ...v, x: v.x - e.deltaX, y: v.y - e.deltaY })
      }
    }

    frame.addEventListener('touchstart', onTouchStart, { passive: true })
    frame.addEventListener('touchmove', onTouchMove, { passive: false })
    frame.addEventListener('touchend', onTouchEnd, { passive: false })
    frame.addEventListener('touchcancel', onTouchEnd, { passive: false })
    frame.addEventListener('pointerdown', onPointerDown)
    frame.addEventListener('pointermove', onPointerMove)
    frame.addEventListener('pointerup', onPointerUp)
    frame.addEventListener('pointercancel', onPointerUp)
    frame.addEventListener('dblclick', onDoubleClick)
    frame.addEventListener('wheel', onWheel, { passive: false })
    return () => {
      frame.removeEventListener('touchstart', onTouchStart)
      frame.removeEventListener('touchmove', onTouchMove)
      frame.removeEventListener('touchend', onTouchEnd)
      frame.removeEventListener('touchcancel', onTouchEnd)
      frame.removeEventListener('pointerdown', onPointerDown)
      frame.removeEventListener('pointermove', onPointerMove)
      frame.removeEventListener('pointerup', onPointerUp)
      frame.removeEventListener('pointercancel', onPointerUp)
      frame.removeEventListener('dblclick', onDoubleClick)
      frame.removeEventListener('wheel', onWheel)
    }
  }, [])

  return (
    <div
      ref={frameRef}
      className={`ui-zoomimage${view.z > 1 ? ' is-zoomed' : ''}`}
      {...DRAG_IGNORE_PROPS}
    >
      <img
        className={className}
        src={src}
        alt={alt}
        draggable={false}
        style={{ transform: `translate(${view.x}px, ${view.y}px) scale(${view.z})` }}
      />
    </div>
  )
}
