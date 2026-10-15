// What a screen that computes its own emptiness tells its first-visit intro: still empty (set it
// up) or holding data (walk through it). Cash Flow decides from a projection only it fetches,
// so it reports here rather than the intro fetching that projection a second time.
import { useSyncExternalStore } from 'react'

export type ScreenFill = 'empty' | 'filled'

const states = new Map<string, ScreenFill>()
const listeners = new Set<() => void>()

export function reportScreenFill(screen: string, fill: ScreenFill): void {
  if (states.get(screen) === fill) return
  states.set(screen, fill)
  listeners.forEach((l) => l())
}

export function useScreenFill(screen: string): ScreenFill | null {
  return useSyncExternalStore(
    (l) => {
      listeners.add(l)
      return () => listeners.delete(l)
    },
    () => states.get(screen) ?? null,
  )
}
