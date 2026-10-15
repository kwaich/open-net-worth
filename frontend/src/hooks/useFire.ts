import { useCallback } from 'react'
import { authFetch } from '../api/client'
import { useQueryCache } from '../state/queryCache'

export interface TrajectoryPoint {
  year: number
  // null when the profile has no date of birth (MATH-28) — the NW/target rows still
  // plot, only the age-based x-axis label degrades.
  age: number | null
  nw: number
  target: number
}

export interface FireResult {
  fire_number: number
  current_nw: number
  progress_pct: number
  monthly_retirement_spending: number
  monthly_savings: number
  expected_return_pct: number
  withdrawal_rate_pct: number
  // null when the profile has no date of birth (MATH-28) — every age-anchored field
  // below (current_age, projected_fire_age, on_track) degrades together rather than
  // fabricating age 0 and a confident verdict off it.
  current_age: number | null
  retirement_target_age: number
  years_to_fire: number | null
  projected_fire_age: number | null
  on_track: boolean | null
  monthly_savings_needed: number | null
  trajectory: TrajectoryPoint[]
  retirement_duration_years: number
  recommended_swr_pct: number
  swr_warning: boolean
  volatility_pct: number
  life_expectancy: number
}

/** Throws on a non-2xx so the failure lands in the cache entry's `error`, leaving any
 *  last-good projection intact. */
async function fetchFire(nw: number): Promise<FireResult> {
  const res = await authFetch(`/api/fire?current_nw=${nw}`)
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  return (await res.json()) as FireResult
}

interface UseFireResult {
  data: FireResult | null
  loading: boolean
  error: boolean
  retry: () => void
}

/**
 * GET /api/fire?current_nw=X. When currentNw <= 0, the fetch is skipped.
 */
export function useFire(currentNw: number): UseFireResult {
  // Cache-backed so a reopened app shows the last FIRE projection at once and refreshes it
  // underneath (2026-07-31). The key carries the rounded net worth: a different net worth is a
  // different answer, and must not be served from the previous one's entry.
  const nw = Math.round(currentNw)
  const key = `/api/fire?current_nw=${nw}`
  const query = useQueryCache<FireResult>(key, () => fetchFire(nw), { enabled: nw > 0 })

  return {
    data: query.data ?? null,
    loading: query.initialLoading,
    // Only an error with nothing to show is an error state — a failed BACKGROUND refresh must
    // leave the last good projection on screen.
    error: !!query.error && query.data === undefined,
    retry: useCallback(() => {
      void query.refresh()
    }, [query]),
  }
}
