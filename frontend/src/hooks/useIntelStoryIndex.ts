import { useMemo } from 'react'
import { authFetch } from '../api/client'
import { useQueryCache } from '../state/queryCache'
import type { StoryArc } from '../components/intel/storyMeta'

// The Intel feed read, shared by key with the Intel tab itself (state/queryCache dedupes by key),
// so the Investments holdings rows can show "this name has a live story" at no extra fetch cost.
export const INTEL_SIGNALS_KEY = '/api/intel/signals?include_archived=true'
// One-shot hand-off: the Intel tab focuses this story when it next mounts (then clears it).
export const INTEL_FOCUS_KEY = 'intel-focus-story'
export const INTEL_SUBTAB_KEY = 'intel-subtab'

export interface TickerStories {
  count: number
  newestId: string
  unread: number
}

async function fetchSignals(): Promise<StoryArc[]> {
  const res = await authFetch(INTEL_SIGNALS_KEY)
  if (!res.ok) throw new Error(String(res.status))
  return res.json()
}

/** Upper-cased ticker → its LIVE (not dismissed/resolved/archived) Intel stories. */
export function useIntelStoryIndex(): Map<string, TickerStories> {
  const { data } = useQueryCache<StoryArc[]>(INTEL_SIGNALS_KEY, fetchSignals)
  return useMemo(() => {
    const index = new Map<string, TickerStories & { newestAt: string }>()
    for (const s of data ?? []) {
      if (s.dismissed || s.resolved || s.archived || !s.ticker) continue
      const key = s.ticker.toUpperCase()
      const cur = index.get(key)
      if (!cur) {
        index.set(key, { count: 1, newestId: s.id, newestAt: s.updated_at, unread: s.read ? 0 : 1 })
        continue
      }
      cur.count += 1
      cur.unread += s.read ? 0 : 1
      if ((s.updated_at || '') > cur.newestAt) {
        cur.newestId = s.id
        cur.newestAt = s.updated_at
      }
    }
    return index
  }, [data])
}

/** Ask the Intel tab to open on Holdings with this story focused. */
export function requestIntelStoryFocus(storyId: string): void {
  try {
    sessionStorage.setItem(INTEL_FOCUS_KEY, storyId)
    sessionStorage.setItem(INTEL_SUBTAB_KEY, 'holdings')
  } catch {
    /* storage unavailable — the Intel tab still opens, just without the focus */
  }
}

/** Read (and clear) a pending focus request. */
export function takeIntelStoryFocus(): string | null {
  try {
    const id = sessionStorage.getItem(INTEL_FOCUS_KEY)
    if (id) sessionStorage.removeItem(INTEL_FOCUS_KEY)
    return id
  } catch {
    return null
  }
}
