import { useSyncExternalStore } from 'react'
import type { TrendPeriod } from '../types/watchPulse'

// The pulse rows' trend period (1M · 3M · 1Y) — one per viewer, shared by the Holdings and
// watchlist tabs and by the sort that ranks on it, so switching it anywhere redraws everywhere.
// Remembered in localStorage (a per-viewer convenience; blocked storage just means 1M).

export const TREND_PERIODS: TrendPeriod[] = ['1M', '3M', '1Y']
const KEY = 'intel-pulse-period'
const listeners = new Set<() => void>()

function read(): TrendPeriod {
  try {
    const v = localStorage.getItem(KEY)
    return TREND_PERIODS.includes(v as TrendPeriod) ? (v as TrendPeriod) : '1M'
  } catch {
    return '1M'
  }
}

let current: TrendPeriod = read()

export function setPulsePeriod(p: TrendPeriod): void {
  current = p
  try {
    localStorage.setItem(KEY, p)
  } catch {
    /* per-viewer convenience only */
  }
  listeners.forEach((l) => l())
}

function subscribe(l: () => void): () => void {
  listeners.add(l)
  return () => listeners.delete(l)
}

export function usePulsePeriod(): [TrendPeriod, (p: TrendPeriod) => void] {
  const p = useSyncExternalStore(subscribe, () => current)
  return [p, setPulsePeriod]
}

/** Test seam: re-read storage (a test that seeds localStorage before mounting). */
export function resetPulsePeriodForTests(): void {
  current = read()
}
