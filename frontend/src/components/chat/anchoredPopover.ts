import { useCallback, useEffect, useLayoutEffect, useState, type RefObject } from 'react'

const GAP = 4
const EDGE = 8

export interface PopoverPlacement {
  top: number
  left: number
  above: boolean
}

/** Where a popover anchored to `trigger` goes, in viewport coordinates: below it, right edges
 *  aligned, unless that would run off the bottom of the window and there is more room above —
 *  then above it. Always clamped inside the window, so its buttons stay reachable. */
export function placeBelowOrAbove(
  trigger: Pick<DOMRect, 'top' | 'bottom' | 'right'>,
  popover: { width: number; height: number },
  viewport: { width: number; height: number },
): PopoverPlacement {
  const roomBelow = viewport.height - trigger.bottom - GAP - EDGE
  const roomAbove = trigger.top - GAP - EDGE
  const above = popover.height > roomBelow && roomAbove > roomBelow
  const wanted = above ? trigger.top - GAP - popover.height : trigger.bottom + GAP
  const top = Math.max(EDGE, Math.min(wanted, viewport.height - popover.height - EDGE))
  const left = Math.max(
    EDGE,
    Math.min(trigger.right - popover.width, viewport.width - popover.width - EDGE),
  )
  return { top, left, above }
}

/** Fixed-position placement for a popover portaled to <body>, kept on its trigger while the
 *  thread scrolls or the window resizes. `contentKey` (a string, so it compares by value)
 *  changes whenever the popover's content — and so its height — does: a preview landing
 *  after open must re-place it. Null until the
 *  first measurement: render the popover hidden until then so it never flashes at 0,0. */
export function useAnchoredPopover(
  open: boolean,
  triggerRef: RefObject<HTMLElement | null>,
  popoverRef: RefObject<HTMLElement | null>,
  contentKey: string,
): PopoverPlacement | null {
  const [placement, setPlacement] = useState<PopoverPlacement | null>(null)

  const place = useCallback(() => {
    const trigger = triggerRef.current
    const popover = popoverRef.current
    if (!trigger || !popover) return
    const box = popover.getBoundingClientRect()
    const next = placeBelowOrAbove(
      trigger.getBoundingClientRect(),
      { width: box.width, height: box.height },
      { width: window.innerWidth, height: window.innerHeight },
    )
    // Same spot → same object, so a re-measure that changes nothing re-renders nothing.
    setPlacement((prev) =>
      prev && prev.top === next.top && prev.left === next.left && prev.above === next.above
        ? prev
        : next,
    )
  }, [triggerRef, popoverRef])

  // Before paint, so a reopened popover never shows at its last position for a frame.
  useLayoutEffect(() => {
    if (open) place()
  }, [open, place, contentKey])

  useEffect(() => {
    if (!open) return
    window.addEventListener('scroll', place, true)
    window.addEventListener('resize', place)
    return () => {
      window.removeEventListener('scroll', place, true)
      window.removeEventListener('resize', place)
    }
  }, [open, place])

  return open ? placement : null
}
