import { useEffect, useState } from 'react'
import { authFetch } from '../api/client'
import type { AssetDetail } from '../types/assetDetail'
import { seedDetailCache } from './useAssetDetail'

interface UseResolveTickerResult {
  /** null while resolving/on failure — the resolved instrument id once known. */
  instrumentId: string | null
  resolving: boolean
  error: boolean
  retry: () => void
}

/** Resolves a ticker (e.g. "KSTL.DE") to its instrument id via the one-hop
 *  GET /api/intel/assets/by-ticker/{ticker} (find-or-create + full detail), seeding
 *  useAssetDetail's cache so the subsequent id-keyed AssetDetailView paints instantly
 *  instead of re-fetching. This is the /quote/$symbol route's sole data dependency —
 *  navigation to that route never waits on this call; only the destination screen's
 *  content does. */
export function useResolveTicker(ticker: string): UseResolveTickerResult {
  const [instrumentId, setInstrumentId] = useState<string | null>(null)
  const [resolving, setResolving] = useState(true)
  const [error, setError] = useState(false)
  const [attempt, setAttempt] = useState(0)

  useEffect(() => {
    let alive = true
    setInstrumentId(null)
    setResolving(true)
    setError(false)

    authFetch(`/api/intel/assets/by-ticker/${encodeURIComponent(ticker)}`)
      .then(async (res) => {
        if (!res.ok) {
          if (alive) setError(true)
          return
        }
        const data: AssetDetail = await res.json()
        if (!alive) return
        seedDetailCache(data.instrument_id, data)
        setInstrumentId(data.instrument_id)
      })
      .catch(() => {
        if (alive) setError(true)
      })
      .finally(() => {
        if (alive) setResolving(false)
      })

    return () => {
      alive = false
    }
  }, [ticker, attempt])

  return { instrumentId, resolving, error, retry: () => setAttempt((n) => n + 1) }
}
