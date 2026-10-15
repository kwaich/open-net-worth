import { useCallback } from 'react'
import { authFetch } from '../api/client'
import { invalidateQueryCache, useQueryCache } from '../state/queryCache'
import type { IntelDigest } from '../types/intelDigest'

// The digest is read through `state/queryCache`, which mirrors itself to localStorage. That
// replaced a module-level Map: it did dedupe within a session, but it died with the JS context,
// so every reopen of the installed PWA re-blanked the brief card and re-fetched (2026-07-31).
// A manual scan still forces a refetch via `refetch()`.
const DIGEST_KEY = '/api/intel/digest?period=1M&narrate=false'

/** Test-only: drop the cached digest between cases. */
export function _clearDigestCache(): void {
  invalidateQueryCache((key) => key === DIGEST_KEY)
}

export interface UseIntelDigestResult {
  data: IntelDigest | null
  loading: boolean
  error: boolean
  refetch: () => void
}

async function fetchDigest(): Promise<IntelDigest> {
  const res = await authFetch(DIGEST_KEY)
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  return (await res.json()) as IntelDigest
}

export function useIntelDigest(): UseIntelDigestResult {
  const query = useQueryCache<IntelDigest>(DIGEST_KEY, fetchDigest)

  return {
    data: query.data ?? null,
    loading: query.initialLoading,
    // A failed refresh with a last-good digest still on screen is not an error state.
    error: !!query.error && query.data === undefined,
    refetch: useCallback(() => {
      void query.refresh()
    }, [query]),
  }
}

// The Holdings brief at verbosity 5: Lucius's short narrative, written server-side from the same
// digest figures and fact-checked there (monitoring/brief_narration.py). A separate key so the
// plain digest above stays fast; the server caches the narrative for 30 minutes, so this doesn't
// need to revalidate on every mount either.
const NARRATED_KEY = '/api/intel/digest?period=1M&narrate=true&style=brief'
const NARRATION_STALE_MS = 15 * 60_000

export interface BriefNarration {
  narration: string | null
  opinion: IntelDigest['narration_opinion']
}

export function useHoldingsNarration(enabled: boolean): {
  data: BriefNarration | null
  loading: boolean
} {
  const query = useQueryCache<IntelDigest>(
    NARRATED_KEY,
    async () => {
      const res = await authFetch(NARRATED_KEY)
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      return (await res.json()) as IntelDigest
    },
    { enabled, staleTime: NARRATION_STALE_MS },
  )
  const d = enabled ? query.data : undefined
  return {
    data: d ? { narration: d.narration, opinion: d.narration_opinion ?? null } : null,
    loading: query.initialLoading,
  }
}
