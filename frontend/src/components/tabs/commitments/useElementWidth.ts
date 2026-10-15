// Measured pixel width of an element.
//
// Exists for one reason: a viewBox'd SVG at `width: 100%` derives its HEIGHT from the viewBox's
// aspect ratio, so a `0 0 760 152` chart is 152px tall at 760px and 276px tall at 1380px —
// the same drawing inflated, empty plot area and all. Pinning a CSS height instead is the trap
// documented in chart.tsx and useNarrow.ts: with `preserveAspectRatio="none"` it stretches Y
// alone, turning data dots into ellipses and detaching the bar band from its plot.
//
// Feeding the MEASURED width back in as the viewBox width makes the box map 1:1 to CSS pixels.
// The height is then whatever the viewBox says, at every pane width, and all the vertical
// spacing stays tuned exactly as it was — extra width only buys more room per month.
import { useCallback, useEffect, useLayoutEffect, useState, type RefObject } from 'react'

export function useElementWidth(ref: RefObject<HTMLElement | null>): number {
  const [width, setWidth] = useState(0)

  const measure = useCallback(() => {
    const el = ref.current
    if (el) setWidth(el.clientWidth)
  }, [ref])

  // Layout effect so the first paint already has the real width — a frame at the fallback
  // box would otherwise show as a visible resize jump on load.
  useLayoutEffect(() => {
    measure()
  }, [measure])

  useEffect(() => {
    const el = ref.current
    if (!el || typeof ResizeObserver === 'undefined') return
    const ro = new ResizeObserver(measure)
    ro.observe(el)
    window.addEventListener('resize', measure)
    return () => {
      ro.disconnect()
      window.removeEventListener('resize', measure)
    }
  }, [measure])

  return width
}
