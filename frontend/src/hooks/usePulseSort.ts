// usePulseSort — the pulse list's sort, remembered per viewer: one for My holdings, one shared by
// every watchlist tab. A per-viewer convenience only: storage that throws or comes back empty
// falls back to "Most urgent".

import { useCallback, useState } from 'react'
import { isPulseSort, type PulseSort } from '../components/intel/pulseHelp'

export const PULSE_SORT_KEYS = {
  holdings: 'intel-pulse-sort-held',
  lists: 'intel-pulse-sort',
} as const

function load(key: string): PulseSort {
  try {
    const v = localStorage.getItem(key)
    return isPulseSort(v) ? v : 'attention'
  } catch {
    return 'attention'
  }
}

export function usePulseSort(key: string): [PulseSort, (next: PulseSort) => void] {
  const [state, setState] = useState(() => ({ key, sort: load(key) }))
  // A new key is a different remembered sort — re-read it (adjusted during render), so a write
  // never lands under one key while the state still shows the other's.
  let current = state
  if (state.key !== key) {
    current = { key, sort: load(key) }
    setState(current)
  }
  const setSort = useCallback(
    (next: PulseSort) => {
      setState({ key, sort: next })
      try {
        localStorage.setItem(key, next)
      } catch {
        /* per-viewer convenience only */
      }
    },
    [key],
  )
  return [current.sort, setSort]
}
