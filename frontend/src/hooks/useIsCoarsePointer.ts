// useIsCoarsePointer — whether the primary pointing device is touch-like.
//
// This intentionally uses `pointer`, not `any-pointer`: an iPad with a hardware
// keyboard can still have touch as its primary input, so opening a read-oriented
// panel must not raise the software keyboard merely because a mouse is available.

import { useCallback, useSyncExternalStore } from 'react'

const QUERY = '(pointer: coarse)'

export function useIsCoarsePointer(): boolean {
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
