/**
 * useBreakpoint — reactive viewport-width breakpoint hook.
 *
 * Returns the current breakpoint name ('sm' | 'md' | 'lg' | 'xl') driven by
 * matchMedia listeners.  Computes the minimum breakpoint whose min-width is
 * satisfied by the current viewport.  Falls back to 'sm' when the viewport is
 * below the `sm` threshold (< 640px).
 *
 * Usage:
 *   const bp = useBreakpoint()
 *   if (bp === 'sm') { ... }              // < md (i.e. < 768px)
 *   if (bp === 'lg' || bp === 'xl') { ... } // desktop
 *
 * RFC: .claude/rfcs/phaseD-responsive.md  (Phase D — D-7)
 */
import { useState, useEffect } from 'react'
import { BP } from '../components/ui/breakpoints'

export type BreakpointName = 'sm' | 'md' | 'lg' | 'xl'

// Ordered from widest to narrowest so we can find the highest active step.
const STEPS: Array<[BreakpointName, number]> = [
  ['xl', BP.xl],
  ['lg', BP.lg],
  ['md', BP.md],
  ['sm', BP.sm],
]

function currentBreakpoint(): BreakpointName {
  for (const [name, minPx] of STEPS) {
    if (window.matchMedia(`(min-width: ${minPx}px)`).matches) return name
  }
  return 'sm' // below the smallest step
}

export function useBreakpoint(): BreakpointName {
  const [bp, setBp] = useState<BreakpointName>(() =>
    typeof window === 'undefined' ? 'lg' : currentBreakpoint(),
  )

  useEffect(() => {
    const queries = STEPS.map(([, minPx]) => window.matchMedia(`(min-width: ${minPx}px)`))
    const handler = () => setBp(currentBreakpoint())
    queries.forEach((q) => q.addEventListener('change', handler))
    // Sync on mount in case SSR initial guess was wrong
    handler()
    return () => queries.forEach((q) => q.removeEventListener('change', handler))
  }, [])

  return bp
}
