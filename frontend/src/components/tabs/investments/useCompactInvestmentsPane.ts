import { useLayoutEffect, useState } from 'react'

// Keep this identical to index.css's `.intel-layout` container-query boundary. Below it the
// feed and 332px cockpit cannot remain side-by-side, so the cockpit becomes a sub-tab instead
// of being pushed beneath every holding.
const COCKPIT_SUBTAB_MAX = 860

export function resolveInvestmentActiveTab<T extends string>(
  selectedTab: T,
  cockpitInSubtab: boolean,
  bookEmpty: boolean,
): T | 'holdings' {
  return selectedTab === 'analysis' && (!cockpitInSubtab || bookEmpty) ? 'holdings' : selectedTab
}

export function useCompactInvestmentsPane(isMobile: boolean) {
  // A callback ref, not useRef: the anchor is absent on the first render while the book loads
  // (a skeleton stands in), and a once-only effect that found no anchor never measured again —
  // the pane then kept its first-render guess forever (a window widened from phone size kept
  // the cockpit as a sub-tab while the grid laid out two columns, leaving the side empty).
  const [anchor, paneAnchorRef] = useState<HTMLDivElement | null>(null)
  const [narrowPane, setNarrowPane] = useState(isMobile)

  useLayoutEffect(() => {
    const pane = anchor?.closest('.intel-tab') as HTMLElement | null
    if (!pane) return
    const measure = () => {
      setNarrowPane(pane.getBoundingClientRect().width <= COCKPIT_SUBTAB_MAX)
    }
    measure()
    if (typeof ResizeObserver === 'undefined') {
      window.addEventListener('resize', measure)
      return () => window.removeEventListener('resize', measure)
    }
    const observer = new ResizeObserver(measure)
    observer.observe(pane)
    return () => observer.disconnect()
  }, [anchor])

  return { paneAnchorRef, cockpitInSubtab: isMobile || narrowPane }
}
