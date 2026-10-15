import { authFetch } from '../../api/client'

/**
 * The one fetcher body every serverState hook shares.
 *
 * Deliberately MODULE-LEVEL, never a render-local closure: `queryCache.entryFor()` reassigns
 * `entry.fetcher` on every call, so if each of the 18 components reading `/api/accounts`
 * passed its own closure, whichever rendered last would silently define what a later
 * `refresh()` / `invalidateQueryCache()` actually re-fetches. One stable function reference
 * per key makes that last-writer-wins hazard structurally impossible.
 *
 * Throws on a non-2xx so the failure lands in the cache entry's `error` (and leaves any
 * last-good `data` intact) rather than poisoning the cache with a parsed error body.
 */
export async function fetchJson<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await authFetch(path, init)
  if (!res.ok) throw new Error(`${path} → HTTP ${res.status}`)
  return (await res.json()) as T
}
