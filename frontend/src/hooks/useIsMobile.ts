// useIsMobile — true below the phone breakpoint (--below-sm, <640px).
//
// The mobile shell (bottom tab bar + Lucius sheet) is width-gated only, never
// pointer-gated, so a narrow desktop window gets the same shell and the whole
// thing stays testable in a browser (RFC §3.1).
//
// useSyncExternalStore rather than useState+useEffect: matchMedia IS an external
// store, and subscribing this way keeps the first render and any resize that
// happens before the effect flushes consistent.

import { useCallback, useSyncExternalStore } from 'react'

const QUERY = '(max-width: 639.98px)'

export function useIsMobile(): boolean {
  const subscribe = useCallback((onChange: () => void) => {
    const mq = window.matchMedia(QUERY)
    mq.addEventListener('change', onChange)
    return () => mq.removeEventListener('change', onChange)
  }, [])

  return useSyncExternalStore(
    subscribe,
    () => window.matchMedia(QUERY).matches,
    () => false,
  )
}
