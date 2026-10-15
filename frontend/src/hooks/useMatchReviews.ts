import { useCallback, useEffect, useState } from 'react'
import { fetchCachedQuery } from '../state/queryCache'
import { ACCOUNTS_KEY, fetchJson } from './serverState'
import {
  listMatchReviews,
  acceptReview as apiAcceptReview,
  dismissReview as apiDismissReview,
  judgeMatches,
  undoMerge as apiUndoMerge,
} from '../api/matches'
import type { MatchReview } from '../api/matches'

interface MatchReviewsState {
  reviews: MatchReview[]
  groups: Record<string, MatchReview[]>
  count: number
  supersededCount: number
  loading: boolean
  error: string | null
  /** id → account name, built from GET /api/accounts on mount */
  accountNames: Map<string, string>
}

export function useMatchReviews() {
  const [state, setState] = useState<MatchReviewsState>({
    reviews: [],
    groups: {},
    count: 0,
    supersededCount: 0,
    loading: true,
    error: null,
    accountNames: new Map(),
  })

  const load = useCallback(async () => {
    setState((s) => ({ ...s, loading: true, error: null }))
    try {
      // Accounts here are only an id→name map for display. Reading them through the shared
      // cache entry (rather than this hook's own `/api/accounts` request) removes what was a
      // second, independent fetch of the app's most-duplicated endpoint.
      const [data, accounts] = await Promise.all([
        listMatchReviews(),
        fetchCachedQuery<{ id: string; name: string }[]>(ACCOUNTS_KEY, () =>
          fetchJson<{ id: string; name: string }[]>(ACCOUNTS_KEY),
        ).catch(() => [] as { id: string; name: string }[]),
      ])
      const accountNames = new Map<string, string>(
        (accounts as { id: string; name: string }[]).map((a) => [a.id, a.name]),
      )
      setState({
        reviews: data.reviews ?? [],
        groups: data.groups ?? {},
        count: data.count ?? 0,
        supersededCount: data.superseded_count ?? 0,
        loading: false,
        error: null,
        accountNames,
      })
    } catch (e) {
      setState((s) => ({
        ...s,
        loading: false,
        error: e instanceof Error ? e.message : 'Failed to load duplicate reviews',
      }))
    }
  }, [])

  useEffect(() => {
    // Defer to a microtask so the synchronous setState inside load() doesn't run
    // in the effect body (react-hooks/set-state-in-effect).
    void Promise.resolve().then(load)
  }, [load])

  /** Accept a single review. Optimistically removes from local list; reloads on failure. */
  const acceptReview = useCallback(
    async (id: string): Promise<{ error: string | null; canonicalDescription: string | null }> => {
      setState((s) => ({
        ...s,
        reviews: s.reviews.filter((r) => r.id !== id),
        count: Math.max(0, s.count - 1),
      }))
      try {
        const result = await apiAcceptReview(id)
        return { error: null, canonicalDescription: result.canonical?.description ?? null }
      } catch (e) {
        await load()
        return {
          error: e instanceof Error ? e.message : 'Could not reach the server.',
          canonicalDescription: null,
        }
      }
    },
    [load],
  )

  /** Dismiss a single review. Optimistically removes from local list; reloads on failure. */
  const dismissReview = useCallback(
    async (id: string): Promise<string | null> => {
      setState((s) => ({
        ...s,
        reviews: s.reviews.filter((r) => r.id !== id),
        count: Math.max(0, s.count - 1),
      }))
      try {
        await apiDismissReview(id)
        return null
      } catch (e) {
        await load()
        return e instanceof Error ? e.message : 'Could not reach the server.'
      }
    },
    [load],
  )

  /** Undo a prior merge. No optimistic update — reloads after. Returns error or null. */
  const undoMerge = useCallback(
    async (absorbed_id: string): Promise<string | null> => {
      try {
        await apiUndoMerge(absorbed_id)
        await load()
        return null
      } catch (e) {
        await load()
        return e instanceof Error ? e.message : 'Could not reach the server.'
      }
    },
    [load],
  )

  /** Fire judgeMatches then reload. Returns the result object or null on error. */
  const judgeAll = useCallback(async (): Promise<{
    auto_merged: number
    kept_for_review: number
    judged: number
    cost_capped: boolean
  } | null> => {
    try {
      const result = await judgeMatches()
      await load()
      return result
    } catch {
      await load()
      return null
    }
  }, [load])

  return { ...state, reload: load, acceptReview, dismissReview, undoMerge, judgeAll }
}
