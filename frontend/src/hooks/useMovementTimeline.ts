import { useCallback, useEffect, useState } from 'react'
import { authFetch } from '../api/client'
import type { MovementTimeline } from '../types/assetDetail'

// Session cache so a reopen paints the last-known timeline instantly (stale-while-revalidate),
// mirroring useAssetDetail's detailCache. Keyed by instrument + page size.
const cache = new Map<string, MovementTimeline>()

/** Test-only: reset the module cache between cases. */
export function _clearMovementCache(): void {
  cache.clear()
}

// Poll cadence: fast while any row is still being written up (the paragraph lands in the
// background and should appear within seconds, not at the next 5-minute tick), near-real-time
// while the venue trades (the live row moves on this clock), relaxed when it's shut.
export const PENDING_POLL_MS = 15_000
export const OPEN_POLL_MS = 60_000
export const CLOSED_POLL_MS = 5 * 60_000

export function pollIntervalFor(data: MovementTimeline | null): number {
  if (data?.rows.some((r) => r.narration === 'pending' || r.rewriting)) return PENDING_POLL_MS
  const m = data?.market
  if (!m) return CLOSED_POLL_MS
  return m.is_open || m.always_open ? OPEN_POLL_MS : CLOSED_POLL_MS
}

const keyOf = (instrumentId: string, limit: number) => `${instrumentId}|${limit}`

/** The best page already cached for this instrument: the exact one, else the LARGEST smaller
 *  page — the base a bigger page is appended to, so loading more never blanks the card. */
function bestCached(instrumentId: string, limit: number): MovementTimeline | null {
  const exact = cache.get(keyOf(instrumentId, limit))
  if (exact) return exact
  let best: { size: number; data: MovementTimeline } | null = null
  for (const [k, data] of cache) {
    const [iid, size] = k.split('|')
    const n = Number(size)
    if (iid === instrumentId && n < limit && (!best || n > best.size)) best = { size: n, data }
  }
  return best?.data ?? null
}

interface View {
  key: string
  data: MovementTimeline | null
  /** First load for this instrument: nothing to show yet (the skeleton state). */
  loading: boolean
  /** A bigger page is in flight while a smaller one is on screen (append, never blank). */
  loadingMore: boolean
  error: boolean
}

interface Result {
  data: MovementTimeline | null
  loading: boolean
  loadingMore: boolean
  error: boolean
  refetch: () => Promise<void>
}

function initialView(instrumentId: string, limit: number): View {
  const key = keyOf(instrumentId, limit)
  const data = bestCached(instrumentId, limit)
  return { key, data, loading: !data, loadingMore: !!data && !cache.has(key), error: false }
}

export function useMovementTimeline(instrumentId: string, limit = 10): Result {
  const key = keyOf(instrumentId, limit)
  const [view, setView] = useState<View>(() => initialView(instrumentId, limit))
  // Key changed (another ticker / a bigger page): reset DURING render, the React-sanctioned
  // "adjust state when a prop changes" pattern — no setState inside an effect.
  if (view.key !== key) setView(initialView(instrumentId, limit))

  const fetchData = useCallback(
    async (quiet: boolean) => {
      const myKey = key
      const commit = (patch: Partial<View>) =>
        setView((v) => (v.key === myKey ? { ...v, ...patch } : v))
      try {
        // Every commit below is key-guarded (`commit`), so a response that lands after the
        // hook moved to another ticker/limit is dropped instead of clobbering the new view.
        const res = await authFetch(`/api/intel/assets/${instrumentId}/movements?limit=${limit}`)
        if (!res.ok) {
          // 404 = no ticker / unknown → the section simply hides (data stays null, no error).
          commit({ loading: false, loadingMore: false, error: quiet ? false : res.status !== 404 })
          return
        }
        const d: MovementTimeline = await res.json()
        cache.set(myKey, d)
        commit({ data: d, loading: false, loadingMore: false, error: false })
      } catch {
        commit({ loading: false, loadingMore: false, error: !quiet })
      }
    },
    [instrumentId, limit, key],
  )

  // Initial load / revalidate the cached view whenever the key changes.
  useEffect(() => {
    void fetchData(cache.has(key))
  }, [key, fetchData])

  const current = view.key === key ? view : initialView(instrumentId, limit)
  // Derived from the data ON SCREEN and a dependency of the poll effect below, so the cadence
  // re-arms the moment the first response lands (owner-reproduced 2026-09-20: the timer was
  // armed once, before any data existed, at the closed-market 5 minutes — a row left
  // "Generating…" for an hour while its paragraph sat ready on the server).
  const interval = pollIntervalFor(current.data)

  // Adaptive poll; paused while the tab is hidden.
  useEffect(() => {
    let timer: ReturnType<typeof setTimeout> | null = null
    let cancelled = false
    const tick = async () => {
      if (cancelled) return
      if (typeof document === 'undefined' || document.visibilityState !== 'hidden') {
        await fetchData(true)
      }
      if (!cancelled) timer = setTimeout(tick, pollIntervalFor(cache.get(key) ?? null))
    }
    timer = setTimeout(tick, interval)
    return () => {
      cancelled = true
      if (timer) clearTimeout(timer)
    }
  }, [key, fetchData, interval])

  // Coming back to the tab/screen refetches at once — a phone that slept through the poll
  // shows what landed meanwhile without a manual back-and-reopen.
  useEffect(() => {
    if (typeof document === 'undefined') return
    const onVisible = () => {
      if (document.visibilityState === 'visible') void fetchData(true)
    }
    document.addEventListener('visibilitychange', onVisible)
    return () => document.removeEventListener('visibilitychange', onVisible)
  }, [fetchData])

  const refetch = useCallback(() => fetchData(true), [fetchData])
  return {
    data: current.data,
    loading: current.loading,
    loadingMore: current.loadingMore,
    error: current.error,
    refetch,
  }
}
