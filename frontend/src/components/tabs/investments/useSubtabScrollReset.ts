import { useLayoutEffect, useRef } from 'react'

function scrollerOf(el: HTMLElement): HTMLElement {
  for (let p = el.parentElement; p; p = p.parentElement) {
    if (/(auto|scroll)/.test(getComputedStyle(p).overflowY)) return p
  }
  return (document.scrollingElement as HTMLElement | null) ?? document.documentElement
}

/** The scroll offset at which the tab wrapper reaches the scroller's top — where it pins. */
function stickPoint(tabwrap: HTMLElement, scroller: HTMLElement): number {
  const scrollerTop =
    scroller === document.scrollingElement ? 0 : scroller.getBoundingClientRect().top
  return tabwrap.getBoundingClientRect().top - scrollerTop + scroller.scrollTop
}

/**
 * Keeps the sub-tab bar pinned across a sub-tab switch.
 *
 * 1. The tab wrapper is at least one scroller-viewport tall. Without that, switching to a
 *    tab whose content is shorter than a screen made the pinned offset unreachable: the
 *    browser clamped scrollTop to the new, smaller maximum, the page jumped up and the bar
 *    un-pinned (Holdings → a short watchlist).
 * 2. Switching while pinned scrolls back to exactly where the bar starts sticking, so the new
 *    tab begins at its top rather than part-way down its list at the old offset. Above that
 *    point nothing moves.
 *
 * iOS Safari keeps an in-flight momentum scroll (a tap right after a flick) running over a
 * plain scrollTop write; pausing overflow for one frame cancels the fling, and the frame after
 * re-applies the point in case layout settled late.
 *
 * Takes the ELEMENT, not a ref: on a cold load it mounts after the loading skeleton.
 */
export function useSubtabScrollReset(activeTab: string, tabwrap: HTMLElement | null) {
  useLayoutEffect(() => {
    if (!tabwrap) return
    const scroller = scrollerOf(tabwrap)
    const fit = () => {
      tabwrap.style.setProperty('min-height', `${scroller.clientHeight}px`)
    }
    fit()
    if (typeof ResizeObserver === 'undefined') {
      window.addEventListener('resize', fit)
      return () => {
        window.removeEventListener('resize', fit)
        tabwrap.style.removeProperty('min-height')
      }
    }
    const observer = new ResizeObserver(fit)
    observer.observe(scroller)
    return () => {
      observer.disconnect()
      tabwrap.style.removeProperty('min-height')
    }
  }, [tabwrap])

  const previous = useRef(activeTab)
  useLayoutEffect(() => {
    if (previous.current === activeTab) return
    previous.current = activeTab
    if (!tabwrap) return
    const scroller = scrollerOf(tabwrap)
    const stickAt = stickPoint(tabwrap, scroller)
    if (scroller.scrollTop <= stickAt) return

    const overflow = scroller.style.overflowY
    scroller.style.overflowY = 'hidden'
    scroller.scrollTop = stickAt
    const frame = requestAnimationFrame(() => {
      scroller.style.overflowY = overflow
      const again = stickPoint(tabwrap, scroller)
      if (Math.abs(scroller.scrollTop - again) > 1) scroller.scrollTop = again
    })
    return () => {
      cancelAnimationFrame(frame)
      scroller.style.overflowY = overflow
    }
  }, [activeTab, tabwrap])
}
