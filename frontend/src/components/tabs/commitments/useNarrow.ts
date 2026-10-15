// Shared phone-width signal for the Commitments tab.
//
// Some mobile problems cannot be solved in CSS because they are GEOMETRY, not style: an SVG
// with a viewBox renders at width ÷ aspect-ratio, so a 760×152 chart is ~86px tall on a phone
// no matter what the stylesheet says. Pinning a CSS height "fixes" the height by stretching
// the viewBox non-uniformly — which turns the data dots into ellipses and pushes the bar band
// away from the plot it belongs to. The only correct fix is to hand the chart a different
// viewBox, which means the component has to know it is narrow.
//
// Matches the same 560px the tab's container queries use, so the JS and CSS breakpoints agree.
import { useSyncExternalStore } from 'react'

export const NARROW_QUERY = '(max-width: 560px)'

const subscribe = (onChange: () => void): (() => void) => {
  if (typeof window === 'undefined' || !window.matchMedia) return () => {}
  const mql = window.matchMedia(NARROW_QUERY)
  mql.addEventListener('change', onChange)
  return () => mql.removeEventListener('change', onChange)
}

const getSnapshot = (): boolean =>
  typeof window !== 'undefined' && !!window.matchMedia && window.matchMedia(NARROW_QUERY).matches

// Server/test snapshot: assume desktop so a non-DOM render matches the historical layout.
const getServerSnapshot = (): boolean => false

export function useNarrow(): boolean {
  return useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot)
}
